"""Run LangBridge on official SWE-bench Docker images; grade with the official harness.

Strategy (verified / pro):
  1. Start the official instance image (repo + deps already baked in).
  2. docker cp LangBridge source into the container.
  3. Bootstrap an isolated portable Python 3.12 via uv under ``/opt/lb-venv``
     (does **not** prepend that venv onto PATH — bash ``python``/``pytest`` still
     hit the image's original toolchain).
  4. Run the headless agent with ``/opt/lb-venv/bin/python``.
  5. Capture ``git diff`` → ``predictions.jsonl``.
  6. Grade with the **official** swebench / Scale harness (not our in-container grader).

Own langbridge-bench tasks still use ``eval/run_eval.py`` (in-container grade).
Lite is not supported.

  uv run python eval/run_public_eval.py --difficulty verified --count 10
  # then grade (verified):
  cd eval && uv run python -m swebench.harness.run_evaluation \\
    --dataset_name princeton-nlp/SWE-bench_Verified \\
    --predictions_path out/predictions.jsonl \\
    --max_workers 4 --run_id langbridge-verified
"""

from __future__ import annotations

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
from sandbox.agent_venv import AGENT_PYTHON, bootstrap_agent_venv
from sandbox.docker import container_exec, docker, image_exists

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
MODEL_NAME = "langbridge-l4"

DATASETS = {
    "verified": "princeton-nlp/SWE-bench_Verified",
    "pro": "ScaleAI/SWE-bench_Pro",
}

PRO_DOCKERHUB_REPO = "jefzda/sweap-images"

# Inside the container.
CONTAINER_SRC = "/opt/langbridge/src"
CONTAINER_PKG = f"{CONTAINER_SRC}/langbridge_code"
CONTAINER_ARTIFACTS = "/root/lb_artifacts"
CONTAINER_LANGBRIDGE_ARTIFACTS = "/root/lb_session_artifacts"
CONTAINER_PROBLEM = "/tmp/problem.txt"


def load_instances(dataset_name, split, count, instance_ids=None):
    rows = list(load_dataset(dataset_name, split=split))
    if instance_ids:
        wanted = set(instance_ids)
        return [row for row in rows if row["instance_id"] in wanted]
    if count:
        rows = rows[:count]
    return rows


def ensure_image(image):
    if image_exists(image):
        return
    print(f"  pulling {image} ...")
    result = docker(["pull", image])
    if result.returncode != 0:
        raise RuntimeError(f"docker pull failed for {image}: {result.stderr.strip()}")


def resolve_image_and_repo(instance: dict, *, difficulty: str, namespace: str) -> tuple[str, str]:
    """Return (docker_image, repo_workdir) for this instance."""
    if difficulty == "pro":
        tag = (instance.get("dockerhub_tag") or "").strip()
        if not tag:
            raise RuntimeError(f"missing dockerhub_tag for {instance.get('instance_id')}")
        return f"{PRO_DOCKERHUB_REPO}:{tag}", "/app"

    from swebench.harness.test_spec.test_spec import make_test_spec

    spec = make_test_spec(instance, namespace=namespace)
    image = getattr(spec, "instance_image_key", None) or getattr(
        spec, "instance_image_tag", None
    )
    if not image:
        raise RuntimeError(f"could not resolve image for {instance.get('instance_id')}")
    return image, "/testbed"


def build_agent_env(api_key, model):
    provider = active_api_provider()
    env = {
        "PYTHONPATH": CONTAINER_SRC,
        "LANGBRIDGE_API_PROVIDER": provider,
        "LANGBRIDGE_ARTIFACTS_DIR": CONTAINER_LANGBRIDGE_ARTIFACTS,
    }
    if provider == "moonshot":
        env["MOONSHOT_API_KEY"] = api_key
        env["KIMI_API_KEY"] = api_key
    elif provider == "deepseek":
        env["DEEPSEEK_API_KEY"] = api_key
        env["OPENAI_API_KEY"] = api_key  # some SDKs still read this
    else:
        env["OPENAI_API_KEY"] = api_key
    if API_BASE_URL:
        env["LANGBRIDGE_API_BASE_URL"] = API_BASE_URL
    env["LANGBRIDGE_MODEL"] = model or DEFAULT_MODEL
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


def run_instance(instance, *, difficulty, namespace, artifacts_root, api_key, model, timeout):
    instance_id = instance["instance_id"]
    artifacts_dir = artifacts_root / instance_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    image, repo_dir = resolve_image_and_repo(
        instance, difficulty=difficulty, namespace=namespace
    )
    container = f"langbridge-eval-{instance_id}".replace("__", "_")[:120]

    patch = ""
    returncode = None
    timed_out = False
    error = ""
    artifact_exports: dict[str, str] = {}

    ensure_image(image)

    docker(["rm", "-f", container])
    # Pro images often ship a non-bash entrypoint; override so sleep works.
    started = docker(
        [
            "run",
            "-d",
            "--name",
            container,
            "--entrypoint",
            "/bin/bash",
            image,
            "-c",
            "sleep infinity",
        ]
    )
    if started.returncode != 0:
        raise RuntimeError(f"docker run failed: {started.stderr.strip()}")

    try:
        container_exec(container, f"mkdir -p {CONTAINER_PKG} {CONTAINER_ARTIFACTS}")
        copy = docker(["cp", f"{SRC_PATH}/.", f"{container}:{CONTAINER_PKG}"])
        if copy.returncode != 0:
            raise RuntimeError(f"docker cp src failed: {copy.stderr.strip()}")

        install = bootstrap_agent_venv(container, pythonpath=CONTAINER_SRC, timeout=900)
        (artifacts_dir / "agent_bootstrap.txt").write_text(
            (install.stdout or "") + (install.stderr or ""), encoding="utf-8"
        )
        if install.returncode != 0:
            raise RuntimeError("agent venv bootstrap failed; see agent_bootstrap.txt")

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
                workdir=repo_dir,
            )
            returncode = result.returncode
            stdout, stderr = result.stdout, result.stderr
        except subprocess.TimeoutExpired as expired:
            timed_out = True
            stdout = (
                expired.stdout.decode()
                if isinstance(expired.stdout, bytes)
                else (expired.stdout or "")
            )
            stderr = (
                expired.stderr.decode()
                if isinstance(expired.stderr, bytes)
                else (expired.stderr or "")
            ) + "\n[timed out]"

        (artifacts_dir / "agent_stdout.txt").write_text(stdout or "", encoding="utf-8")
        (artifacts_dir / "agent_stderr.txt").write_text(stderr or "", encoding="utf-8")

        diff = container_exec(
            container,
            f"cd {repo_dir} && git add -A -- ':!todo_list.md' && "
            f"git diff --cached -- ':!todo_list.md'",
        )
        patch = diff.stdout or ""
    except Exception as failure:  # noqa: BLE001
        error = str(failure)
    finally:
        try:
            artifact_exports = export_container_artifacts(container, artifacts_dir)
        except Exception as copy_error:  # noqa: BLE001
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
        "repo_dir": repo_dir,
        "has_patch": bool(patch.strip()),
        "patch_chars": len(patch),
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
        "artifact_exports": artifact_exports,
    }
    return prediction, summary


def print_grade_hint(difficulty: str, predictions_path: Path) -> None:
    if difficulty == "verified":
        print(
            "\nGrade with the official SWE-bench harness:\n"
            f"  cd {PROJECT_ROOT / 'eval'} && uv run python -m swebench.harness.run_evaluation \\\n"
            "    --dataset_name princeton-nlp/SWE-bench_Verified \\\n"
            f"    --predictions_path {predictions_path} \\\n"
            "    --max_workers 4 --run_id langbridge-verified"
        )
    else:
        print(
            "\nPro grading uses Scale's harness (not swebench.run_evaluation):\n"
            "  https://github.com/scaleapi/SWE-bench_Pro-os\n"
            f"  predictions: {predictions_path}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--difficulty",
        choices=list(DATASETS),
        default="verified",
        help="verified (official swebench grade) or pro (Scale grade).",
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
    parser.add_argument("--namespace", default="swebench", help="Docker Hub namespace for verified images.")
    parser.add_argument("--model", default=os.environ.get("LANGBRIDGE_MODEL", ""))
    parser.add_argument("--timeout", type=int, default=1800, help="Per-instance agent timeout (s).")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "eval" / "out"))
    args = parser.parse_args()

    try:
        api_key = load_api_key()
    except (KeyboardInterrupt, EOFError):
        sys.exit("No API key available.")
    if not api_key:
        sys.exit(
            "No API key found. Set DEEPSEEK_API_KEY / MOONSHOT_API_KEY / OPENAI_API_KEY "
            "or create ~/.langbridge-code/config.json before running the eval."
        )

    difficulty = args.difficulty
    dataset = args.dataset or DATASETS[difficulty]
    # If --dataset overrides, infer difficulty when possible.
    if args.dataset:
        for key, name in DATASETS.items():
            if name == args.dataset:
                difficulty = key
                break

    out_dir = Path(args.out)
    artifacts_root = out_dir / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    instances = load_instances(dataset, args.split, args.count, args.instance_ids)
    print(f"Loaded {len(instances)} instances from {dataset} [{args.split}] ({difficulty}).")

    predictions = []
    summaries = []
    for index, instance in enumerate(instances, start=1):
        print(f"\n[{index}/{len(instances)}] {instance['instance_id']} ({instance['repo']})")
        started = time.time()
        prediction, summary = run_instance(
            instance,
            difficulty=difficulty,
            namespace=args.namespace,
            artifacts_root=artifacts_root,
            api_key=api_key,
            model=args.model,
            timeout=args.timeout,
        )
        summary["duration_s"] = round(time.time() - started, 1)
        predictions.append(prediction)
        summaries.append(summary)
        print(
            f"  -> patch: {summary['has_patch']} ({summary['patch_chars']} chars), "
            f"{summary['duration_s']}s, timed_out={summary['timed_out']}"
            + (f", error={summary['error']}" if summary["error"] else "")
        )
        if summary.get("artifact_exports"):
            print(f"  -> traces: {summary['artifact_exports']}")

    predictions_path = out_dir / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(json.dumps(prediction) + "\n")
    (out_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "difficulty": difficulty,
                "dataset": dataset,
                "split": args.split,
                "summaries": summaries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    produced = sum(1 for summary in summaries if summary["has_patch"])
    print(f"\nWrote {predictions_path}")
    print(f"Produced a patch for {produced}/{len(summaries)} instances.")
    print_grade_hint(difficulty, predictions_path)


if __name__ == "__main__":
    main()
