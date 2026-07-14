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
  3. bootstraps a Python 3.12 venv via uv (SWE-bench images ship Python 3.9;
     langbridge-code requires >=3.11) and installs runtime deps,
  4. runs the headless agent in /testbed with the issue text on stdin,
  5. captures `git diff` as the model_patch,
  6. copies LangBridge session traces/artifacts out of the container,
  7. writes predictions.jsonl the official swebench grader can consume.

Run under the docker group, e.g.:
    sg docker -c "uv run python evals/swe-bench/run_eval_docker.py --count 10"
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from datasets import load_dataset
from langbridge_code.settings import (
    API_BASE_URL,
    DEFAULT_MODEL,
    active_api_provider,
    load_api_key,
)
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
CONTAINER_LANGBRIDGE_ARTIFACTS = f"{CONTAINER_SRC}/langbridge_code/artifacts"
CONTAINER_VENV = "/opt/lb-venv"
AGENT_PYTHON = f"{CONTAINER_VENV}/bin/python"
CONTAINER_PROBLEM = "/tmp/problem.txt"
REPO_DIR = "/testbed"

BOOTSTRAP_AGENT_VENV = f"""
set -e
if [ ! -x {AGENT_PYTHON} ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="/root/.local/bin:$PATH"
  uv python install 3.12
  uv venv {CONTAINER_VENV} --python 3.12
  uv pip install --python {AGENT_PYTHON} openai httpx numpy
fi
{AGENT_PYTHON} -c "import langbridge_code.headless"
"""


def load_instances(dataset_name, split, count, instance_ids=None):
    rows = list(load_dataset(dataset_name, split=split))
    if instance_ids:
        wanted = set(instance_ids)
        return [row for row in rows if row["instance_id"] in wanted]
    if count:
        rows = rows[:count]
    return rows


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


def build_agent_env(api_key, model):
    provider = active_api_provider()
    env = {
        "PYTHONPATH": CONTAINER_SRC,
        "LANGBRIDGE_API_PROVIDER": provider,
        "LANGBRIDGE_ARTIFACTS_DIR": CONTAINER_LANGBRIDGE_ARTIFACTS,
        "LANGBRIDGE_RUNS_DIR": f"{CONTAINER_ARTIFACTS}/session-history",
        "LANGBRIDGE_TODO_LIST_PATH": f"{CONTAINER_ARTIFACTS}/todo_list.md",
    }
    if provider == "moonshot":
        env["MOONSHOT_API_KEY"] = api_key
        env["KIMI_API_KEY"] = api_key
    else:
        env["OPENAI_API_KEY"] = api_key
    if API_BASE_URL:
        env["LANGBRIDGE_API_BASE_URL"] = API_BASE_URL
    if model:
        env["LANGBRIDGE_MODEL"] = model
    else:
        env["LANGBRIDGE_MODEL"] = DEFAULT_MODEL
    return env


def _container_has_path(container, path: str) -> bool:
    return container_exec(container, f"test -e {path}").returncode == 0


def _copy_container_path(container, container_path: str, host_dir: Path) -> Path | None:
    """Copy a container file/dir into host_dir, returning the host path or None."""
    if not _container_has_path(container, container_path):
        return None
    host_dir.mkdir(parents=True, exist_ok=True)
    name = Path(container_path).name
    dest = host_dir / name
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    result = docker(["cp", f"{container}:{container_path}", str(host_dir)])
    if result.returncode != 0:
        raise RuntimeError(
            f"docker cp failed for {container_path}: {(result.stderr or result.stdout).strip()}"
        )
    return dest


def export_container_artifacts(container, artifacts_dir: Path) -> dict[str, str]:
    """Pull LangBridge traces/sessions out of the container before it is removed."""
    exports: dict[str, str] = {}
    for key, container_path in (
        ("langbridge_artifacts", CONTAINER_LANGBRIDGE_ARTIFACTS),
        ("lb_artifacts", CONTAINER_ARTIFACTS),
    ):
        try:
            copied = _copy_container_path(container, container_path, artifacts_dir)
        except RuntimeError as error:
            (artifacts_dir / f"{key}_copy_error.txt").write_text(str(error), encoding="utf-8")
            continue
        if copied is not None:
            exports[key] = str(copied.relative_to(artifacts_dir))
    return exports


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
    artifact_exports: dict[str, str] = {}

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

        # Bootstrap an isolated Python 3.12 venv for the agent (testbed is 3.9).
        install = container_exec(
            container,
            f"export PYTHONPATH={CONTAINER_SRC} && {BOOTSTRAP_AGENT_VENV}",
            timeout=900,
            env={"PYTHONPATH": CONTAINER_SRC},
        )
        (artifacts_dir / "agent_bootstrap.txt").write_text(
            (install.stdout or "") + (install.stderr or ""), encoding="utf-8"
        )
        if install.returncode != 0:
            raise RuntimeError("agent venv bootstrap failed; see agent_bootstrap.txt")

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

        env = build_agent_env(api_key, model)

        try:
            result = container_exec(
                container,
                f"{AGENT_PYTHON} -m langbridge_code.headless < {CONTAINER_PROBLEM}",
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
        try:
            artifact_exports = export_container_artifacts(container, artifacts_dir)
        except Exception as copy_error:  # noqa: BLE001 - still remove the container
            error = error or str(copy_error)
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
        "artifact_exports": artifact_exports,
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
    parser.add_argument(
        "--instance-id",
        action="append",
        dest="instance_ids",
        help="Run only this instance id (repeatable). Overrides --count.",
    )
    parser.add_argument("--namespace", default="swebench", help="Docker Hub namespace for prebuilt images.")
    parser.add_argument("--model", default=os.environ.get("LANGBRIDGE_MODEL", ""))
    parser.add_argument("--timeout", type=int, default=1800, help="Per-instance agent timeout (s).")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "evals" / "swe-bench" / "out"))
    args = parser.parse_args()

    try:
        api_key = load_api_key()
    except (KeyboardInterrupt, EOFError):
        sys.exit("No API key available.")
    if not api_key:
        sys.exit(
            "No API key found. Set MOONSHOT_API_KEY / OPENAI_API_KEY or create "
            "~/.langbridge-code/config.json before running the eval."
        )

    dataset = args.dataset or DATASETS[args.difficulty]

    out_dir = Path(args.out)
    artifacts_root = out_dir / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    instances = load_instances(dataset, args.split, args.count, args.instance_ids)
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
        if summary.get("artifact_exports"):
            print(f"  -> traces: {summary['artifact_exports']}")

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
