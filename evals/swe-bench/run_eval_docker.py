"""Generate SWE-bench predictions by running the langbridge agent *inside* each
instance's official SWE-bench Docker image (the "agent-inside-image" / sandbox path).

Why this exists: the host-based runner (run_eval.py) checks out the repo without
its dependencies, so the agent cannot run the repo's tests and usually produces
an empty patch. Each SWE-bench instance image already has the repo checked out at
base_commit *with all dependencies installed*, so here the agent can actually run
`run_tests` / `pytest` and verify its own fix.

Per instance this:
  1. pulls the official image (swebench namespace) if missing,
  2. starts a container and copies the langbridge source into it,
  3. installs `openai` into the container's `testbed` conda env (the only runtime
     dep the headless path needs; numpy/textual/prompt_toolkit are TUI-only),
  4. runs the headless agent in /testbed with the issue text on stdin,
  5. captures `git diff` as the model_patch,
  6. writes predictions.jsonl the official swebench grader can consume.

Run under the docker group, e.g.:
    sg docker -c "uv run python evals/swe-bench/run_eval_docker.py --count 10"
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from datasets import load_dataset
from swebench.harness.test_spec.test_spec import make_test_spec


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
MODEL_NAME = "langbridge-l4"

# SWE-bench variants ordered easy -> hard. Pick one with --difficulty.
DATASETS = {
    "lite": "princeton-nlp/SWE-bench_Lite",          # ~300 self-contained tasks (easy)
    "verified": "princeton-nlp/SWE-bench_Verified",  # 500 human-validated tasks (medium)
    "pro": "ScaleAI/SWE-bench_Pro",                   # enterprise long-horizon tasks (hard)
}

# Inside the container.
CONTAINER_SRC = "/opt/langbridge/src"
CONTAINER_ARTIFACTS = "/root/lb_artifacts"
CONTAINER_PROBLEM = "/tmp/problem.txt"
REPO_DIR = "/testbed"


def load_instances(dataset_name, split, count):
    data = load_dataset(dataset_name, split=split)
    if count:
        data = data.select(range(min(count, len(data))))
    return list(data)


def docker(args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def image_exists(image):
    return docker(["image", "inspect", image]).returncode == 0


def ensure_image(image):
    if image_exists(image):
        return
    print(f"  pulling {image} ...")
    result = docker(["pull", image])
    if result.returncode != 0:
        raise RuntimeError(f"docker pull failed for {image}: {result.stderr.strip()}")


def container_exec(name, command, env=None, timeout=None, workdir=None, stdin_path=None):
    args = ["exec", "-i"]
    if workdir:
        args += ["-w", workdir]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    args += [name, "bash", "-lc", command]

    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    try:
        return subprocess.run(
            ["docker", *args],
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    finally:
        if stdin_path:
            stdin.close()


def run_instance(instance, namespace, artifacts_root, api_key, model, timeout):
    instance_id = instance["instance_id"]
    artifacts_dir = artifacts_root / instance_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    spec = make_test_spec(instance, namespace=namespace)
    image = spec.instance_image_key
    container = f"langbridge-eval-{instance_id}".replace("__", "_")

    patch = ""
    returncode = None
    timed_out = False
    error = ""

    ensure_image(image)

    docker(["rm", "-f", container])
    started = docker(["run", "-d", "--name", container, image, "sleep", "infinity"])
    if started.returncode != 0:
        raise RuntimeError(f"docker run failed: {started.stderr.strip()}")

    try:
        # Copy the agent source into the container.
        container_exec(container, f"mkdir -p {CONTAINER_SRC} {CONTAINER_ARTIFACTS}")
        copy = docker(["cp", f"{SRC_PATH}/.", f"{container}:{CONTAINER_SRC}"])
        if copy.returncode != 0:
            raise RuntimeError(f"docker cp src failed: {copy.stderr.strip()}")

        # The headless path only needs openai at runtime.
        install = container_exec(container, "pip install -q openai", timeout=600)
        (artifacts_dir / "pip_install.txt").write_text(
            (install.stdout or "") + (install.stderr or ""), encoding="utf-8"
        )
        if install.returncode != 0:
            raise RuntimeError("pip install openai failed; see pip_install.txt")

        # Hand the issue text to the agent via stdin.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(instance["problem_statement"])
            problem_path = handle.name
        try:
            cp_problem = docker(["cp", problem_path, f"{container}:{CONTAINER_PROBLEM}"])
            if cp_problem.returncode != 0:
                raise RuntimeError(f"docker cp problem failed: {cp_problem.stderr.strip()}")
        finally:
            os.unlink(problem_path)

        env = {
            "OPENAI_API_KEY": api_key,
            "PYTHONPATH": CONTAINER_SRC,
            "LANGBRIDGE_RUNS_DIR": f"{CONTAINER_ARTIFACTS}/session-history",
            "LANGBRIDGE_TODO_LIST_PATH": f"{CONTAINER_ARTIFACTS}/todo_list.md",
        }
        if model:
            env["LANGBRIDGE_MODEL"] = model

        try:
            result = container_exec(
                container,
                f"python -m langbridge_code.headless < {CONTAINER_PROBLEM}",
                env=env,
                timeout=timeout,
                workdir=REPO_DIR,
            )
            returncode = result.returncode
            stdout, stderr = result.stdout, result.stderr
        except subprocess.TimeoutExpired as expired:
            timed_out = True
            stdout = expired.stdout.decode() if isinstance(expired.stdout, bytes) else (expired.stdout or "")
            stderr = (expired.stderr.decode() if isinstance(expired.stderr, bytes) else (expired.stderr or "")) + "\n[timed out]"

        (artifacts_dir / "agent_stdout.txt").write_text(stdout or "", encoding="utf-8")
        (artifacts_dir / "agent_stderr.txt").write_text(stderr or "", encoding="utf-8")

        diff = container_exec(
            container,
            f"cd {REPO_DIR} && git add -A && git diff --cached",
        )
        patch = diff.stdout or ""
    except Exception as failure:  # noqa: BLE001 - record any setup/runtime failure
        error = str(failure)
    finally:
        docker(["rm", "-f", container])

    prediction = {
        "instance_id": instance_id,
        "model_name_or_path": MODEL_NAME,
        "model_patch": patch,
    }
    summary = {
        "instance_id": instance_id,
        "repo": instance["repo"],
        "image": image,
        "has_patch": bool(patch.strip()),
        "patch_chars": len(patch),
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
    }
    return prediction, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--difficulty",
        choices=list(DATASETS),
        default="lite",
        help="SWE-bench variant by difficulty (lite=easy, verified=medium, pro=hard).",
    )
    parser.add_argument("--dataset", default=None, help="Explicit HF dataset id; overrides --difficulty.")
    parser.add_argument("--split", default="test")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--namespace", default="swebench", help="Docker Hub namespace for prebuilt images.")
    parser.add_argument("--model", default=os.environ.get("LANGBRIDGE_MODEL", ""))
    parser.add_argument("--timeout", type=int, default=1800, help="Per-instance agent timeout (s).")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "evals" / "swe-bench" / "out"))
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        config_path = Path.home() / ".langbridge" / "config.json"
        if config_path.exists():
            api_key = json.loads(config_path.read_text()).get("api_key")
    if not api_key:
        sys.exit("No API key found. Set OPENAI_API_KEY or create ~/.langbridge/config.json.")

    dataset = args.dataset or DATASETS[args.difficulty]

    out_dir = Path(args.out)
    artifacts_root = out_dir / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    instances = load_instances(dataset, args.split, args.count)
    print(f"Loaded {len(instances)} instances from {dataset} [{args.split}].")

    predictions = []
    summaries = []
    for index, instance in enumerate(instances, start=1):
        print(f"\n[{index}/{len(instances)}] {instance['instance_id']} ({instance['repo']})")
        started = time.time()
        prediction, summary = run_instance(
            instance, args.namespace, artifacts_root, api_key, args.model, args.timeout
        )
        summary["duration_s"] = round(time.time() - started, 1)
        predictions.append(prediction)
        summaries.append(summary)
        print(f"  -> patch: {summary['has_patch']} ({summary['patch_chars']} chars), "
              f"{summary['duration_s']}s, timed_out={summary['timed_out']}"
              + (f", error={summary['error']}" if summary["error"] else ""))

    predictions_path = out_dir / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(json.dumps(prediction) + "\n")
    (out_dir / "run_summary.json").write_text(
        json.dumps({"dataset": dataset, "split": args.split, "summaries": summaries}, indent=2),
        encoding="utf-8",
    )

    produced = sum(1 for summary in summaries if summary["has_patch"])
    print(f"\nWrote {predictions_path}")
    print(f"Produced a patch for {produced}/{len(summaries)} instances. Grade with evals/swe-bench/README.md.")


if __name__ == "__main__":
    main()
