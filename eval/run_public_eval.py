"""Run LangBridge on official SWE-bench Docker images; grade with the official harness.

Strategy (verified / pro):
  1. Start the official instance image (repo + deps already baked in).
  2. docker cp LangBridge source into the container.
  3. Bootstrap an isolated portable Python 3.12 via uv under ``/opt/lb-venv``
     (does **not** prepend that venv onto PATH — bash ``python``/``pytest`` still
     hit the image's original toolchain).
  4. Run ``eval/run_agent.py`` with ``/opt/lb-venv/bin/python``.
  5. Capture ``git diff <base_commit>`` → ``predictions.jsonl``.
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
import concurrent.futures
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from datasets import load_dataset
from langbridge_code.settings import (
    API_BASE_URL,
    active_api_provider,
    load_api_key,
)
from sandbox.agent_venv import AGENT_PYTHON, bootstrap_agent_venv
from sandbox.docker import container_exec, copy_json_into_container, docker, image_exists
from util.pro_prompt import (
    format_problem_statement,
    prompt_protocol,
    prompt_sha256,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
EVAL_PATH = PROJECT_ROOT / "eval"
MODEL_NAME = "langbridge-l4"
PUBLIC_EVAL_TIMEOUT_SECONDS = 2 * 60 * 60

DATASETS = {
    "verified": "princeton-nlp/SWE-bench_Verified",
    "pro": "ScaleAI/SWE-bench_Pro",
}

PRO_DOCKERHUB_REPO = "jefzda/sweap-images"
REPO_RUNTIME_DIFF_EXCLUDES = {
    # NodeBB's baked runtime can start Redis and create these unrelated files.
    "NodeBB/NodeBB": (":!appendonlydir", ":!dump.rdb"),
}

# Inside the container.
CONTAINER_SRC = "/opt/langbridge/src"
CONTAINER_PKG = f"{CONTAINER_SRC}/langbridge_code"
CONTAINER_EVAL = "/opt/langbridge/eval"
CONTAINER_PYTHONPATH = f"{CONTAINER_SRC}:{CONTAINER_EVAL}"
CONTAINER_ARTIFACTS = "/root/lb_artifacts"
CONTAINER_LANGBRIDGE_ARTIFACTS = "/root/lb_session_artifacts"
CONTAINER_PROBLEM = "/tmp/problem.txt"

TIMEOUT_SALVAGE_FAILED = "timeout patch salvage failed"


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
    from util.eval_config import agent_defaults, apply_agent_env

    agent = agent_defaults()
    provider = os.environ.get("LANGBRIDGE_API_PROVIDER") or agent["provider"]
    env = apply_agent_env({
        "PYTHONPATH": CONTAINER_PYTHONPATH,
        "PYTHONUNBUFFERED": "1",
        "LANGBRIDGE_API_PROVIDER": provider,
        "LANGBRIDGE_AGENT_STATE_DIR": CONTAINER_ARTIFACTS,
        "LANGBRIDGE_ARTIFACTS_DIR": CONTAINER_LANGBRIDGE_ARTIFACTS,
    })
    provider = env["LANGBRIDGE_API_PROVIDER"]
    if provider == "moonshot":
        env["MOONSHOT_API_KEY"] = api_key
        env["KIMI_API_KEY"] = api_key
    elif provider == "deepseek":
        env["DEEPSEEK_API_KEY"] = api_key
        env["OPENAI_API_KEY"] = api_key  # some SDKs still read this
    elif provider == "anthropic":
        env["ANTHROPIC_API_KEY"] = api_key
    else:
        env["OPENAI_API_KEY"] = api_key
    if API_BASE_URL and provider == active_api_provider():
        env["LANGBRIDGE_API_BASE_URL"] = API_BASE_URL
    # Single-model override only when CLI/env asks; else provider agent_models.
    if model:
        env["LANGBRIDGE_MODEL"] = model
    return env


def pro_resource_policy(instance: dict, *, difficulty: str) -> dict[str, object]:
    """Return the resource controls used for one public-eval container."""
    is_pro = difficulty == "pro"
    is_go = is_pro and str(instance.get("repo_language") or "").lower() == "go"
    return {
        "platform": "linux/amd64" if is_pro else None,
        "cpus": 2 if is_go else None,
        "goflags": "-p=1" if is_go else None,
        "gomaxprocs": 2 if is_go else None,
    }


def docker_run_resource_args(instance: dict, *, difficulty: str) -> list[str]:
    policy = pro_resource_policy(instance, difficulty=difficulty)
    args: list[str] = []
    if policy["platform"]:
        args.extend(["--platform", str(policy["platform"])])
    if policy["cpus"]:
        args.extend(["--cpus", str(policy["cpus"])])
    return args


def apply_instance_resource_env(
    env: dict[str, str], instance: dict, *, difficulty: str
) -> None:
    policy = pro_resource_policy(instance, difficulty=difficulty)
    if policy["goflags"]:
        env["GOFLAGS"] = str(policy["goflags"])
    if policy["gomaxprocs"]:
        env["GOMAXPROCS"] = str(policy["gomaxprocs"])


def prepare_instance_workspace(container: str, repo_dir: str, base_commit: str) -> None:
    """Reset an official image checkout to the dataset's clean base commit."""
    if not str(base_commit or "").strip():
        raise RuntimeError("instance is missing base_commit")
    repo = shlex.quote(repo_dir)
    base = shlex.quote(str(base_commit))
    reset = container_exec(
        container,
        f"set -e; test -d {repo}/.git; cd {repo}; "
        f"git reset --hard {base}; git clean -fdq; "
        "if [ -f .gitmodules ]; then "
        "git submodule update --recursive --force --no-fetch; "
        "git submodule foreach --recursive 'git reset --hard && git clean -fdq'; "
        "fi",
    )
    if reset.returncode != 0:
        detail = ((reset.stderr or "") + (reset.stdout or "")).strip()[-2000:]
        raise RuntimeError(f"reset to base_commit failed: {detail}")
    status = container_exec(
        container,
        f"cd {repo} && git status --porcelain --untracked-files=all",
    )
    if status.returncode != 0:
        raise RuntimeError(
            f"could not verify clean base checkout: {(status.stderr or '').strip()}"
        )
    if (status.stdout or "").strip():
        raise RuntimeError(
            "checkout is still dirty after reset/clean: "
            + (status.stdout or "").strip()[-2000:]
        )


def bootstrap_container_agent_tools(container: str, *, timeout: int = 300):
    """Install ripgrep with the image's native package manager when it is absent."""
    command = """
if command -v rg >/dev/null 2>&1; then
  exit 0
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache ripgrep
elif command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ripgrep
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y ripgrep
elif command -v yum >/dev/null 2>&1; then
  yum install -y ripgrep
else
  echo "No supported package manager can install missing ripgrep" >&2
  exit 127
fi
"""
    return container_exec(container, command, timeout=timeout)


def parse_agent_stdout(stdout: str) -> dict:
    """Return the final JSON report emitted by eval/run_agent.py."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def strip_binary_diff_hunks(patch_text: str) -> str:
    """Drop binary patch sections, matching the Scale harness's patch handling."""
    sections: list[list[str]] = []
    current: list[str] = []
    for line in (patch_text or "").splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return "".join(
        line
        for section in sections
        if not any(
            line.startswith("Binary files ") or line.startswith("GIT binary patch")
            for line in section
        )
        for line in section
    )


def capture_candidate_patch(
    container: str,
    repo_dir: str,
    base_commit: str,
    runtime_excludes: tuple[str, ...] = (),
) -> str:
    """Capture all candidate changes against base, excluding eval/runtime noise."""
    from util.bench import DIFF_EXCLUDE_PATHSPECS, strip_eval_noise

    repo = shlex.quote(repo_dir)
    base = shlex.quote(str(base_commit))
    # todo_list.md and LangBridge state are orchestration artifacts. Repo-specific
    # runtime exclusions are deliberately narrow so legitimate files stay scored.
    excludes = (
        *DIFF_EXCLUDE_PATHSPECS,
        ":!todo_list.md",
        *runtime_excludes,
    )
    noise_paths = " ".join(
        shlex.quote(pathspec.removeprefix(":!")) for pathspec in excludes
    )
    diff = container_exec(
        container,
        f"cd {repo} && git add -A -- . && "
        f"git reset -q {base} -- {noise_paths} && "
        f"git diff {base} --cached -- .",
    )
    if diff.returncode != 0:
        detail = ((diff.stderr or "") + (diff.stdout or "")).strip()[-2000:]
        raise RuntimeError(f"candidate diff capture failed: {detail}")
    return strip_binary_diff_hunks(strip_eval_noise(diff.stdout or ""))


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


def restart_container_for_timeout_salvage(container: str) -> None:
    """Stop every writer, wait for the barrier, then restart the same overlay."""
    for args in (
        ["kill", container],
        ["wait", container],
        ["start", container],
    ):
        result = docker(args, timeout=30)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"docker {' '.join(args)} failed: {detail}")


def salvage_timeout_patch(
    container: str,
    repo_dir: str,
    base_commit: str,
    runtime_excludes: tuple[str, ...] = (),
) -> str:
    """Capture the canonical checkout only after all timed-out writers stop."""
    restart_container_for_timeout_salvage(container)
    return capture_candidate_patch(
        container,
        repo_dir,
        base_commit,
        runtime_excludes,
    )


def require_candidate_patch(patch: str) -> None:
    """Treat a nominal completion without a code change as retryable failure."""
    if not patch.strip():
        raise RuntimeError(
            "agent completed successfully but produced no candidate patch"
        )


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
    timeout_patch_salvage = "not_needed"
    error = ""
    artifact_exports: dict[str, str] = {}
    task_prompt = format_problem_statement(instance, difficulty=difficulty)
    task_prompt_sha256 = prompt_sha256(instance, difficulty=difficulty)
    protocol = prompt_protocol(difficulty)
    resource_policy = pro_resource_policy(instance, difficulty=difficulty)

    ensure_image(image)

    docker(["rm", "-f", container])
    # Pro images often ship a non-bash entrypoint; override so sleep works.
    started = docker(
        [
            "run",
            "-d",
            "--name",
            container,
            *docker_run_resource_args(instance, difficulty=difficulty),
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
        container_exec(
            container,
            f"mkdir -p {CONTAINER_PKG} {CONTAINER_EVAL} "
            f"{CONTAINER_ARTIFACTS} {CONTAINER_LANGBRIDGE_ARTIFACTS}",
        )
        copy = docker(["cp", f"{SRC_PATH}/.", f"{container}:{CONTAINER_PKG}"])
        if copy.returncode != 0:
            raise RuntimeError(f"docker cp src failed: {copy.stderr.strip()}")
        for name in ("util", "prompt", "run_agent.py"):
            source = EVAL_PATH / name
            copy_eval = docker(["cp", str(source), f"{container}:{CONTAINER_EVAL}/"])
            if copy_eval.returncode != 0:
                raise RuntimeError(
                    f"docker cp {name} failed: {(copy_eval.stderr or '').strip()}"
                )

        # Pin the SUT stack from eval/config.json (source of truth).
        from util.eval_config import merge_agent_user_config

        copy_json_into_container(
            container,
            merge_agent_user_config({}),
            "/root/.langbridge/config.json",
        )

        prepare_instance_workspace(container, repo_dir, instance.get("base_commit"))

        native_tools = bootstrap_container_agent_tools(container)
        (artifacts_dir / "native_tools_bootstrap.txt").write_text(
            (native_tools.stdout or "") + (native_tools.stderr or ""),
            encoding="utf-8",
        )
        if native_tools.returncode != 0:
            raise RuntimeError(
                "native agent tool bootstrap failed; see native_tools_bootstrap.txt"
            )

        install = bootstrap_agent_venv(
            container,
            pythonpath=CONTAINER_PYTHONPATH,
            timeout=900,
        )
        (artifacts_dir / "agent_bootstrap.txt").write_text(
            (install.stdout or "") + (install.stderr or ""), encoding="utf-8"
        )
        if install.returncode != 0:
            raise RuntimeError("agent venv bootstrap failed; see agent_bootstrap.txt")

        (artifacts_dir / "agent_prompt.txt").write_text(
            task_prompt,
            encoding="utf-8",
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(task_prompt)
            problem_path = handle.name
        try:
            cp_problem = docker(["cp", problem_path, f"{container}:{CONTAINER_PROBLEM}"])
            if cp_problem.returncode != 0:
                raise RuntimeError(f"docker cp problem failed: {cp_problem.stderr.strip()}")
        finally:
            os.unlink(problem_path)

        env = build_agent_env(api_key, model)
        agent_seconds = max(60, int(timeout) - 180)
        env["LANGBRIDGE_MAX_AGENT_SECONDS"] = str(agent_seconds)
        env["LANGBRIDGE_FINALIZE_RESERVE_SECONDS"] = str(
            min(300, max(0, agent_seconds // 3))
        )
        apply_instance_resource_env(env, instance, difficulty=difficulty)

        try:
            result = container_exec(
                container,
                f"{AGENT_PYTHON} -u {CONTAINER_EVAL}/run_agent.py "
                f"< {CONTAINER_PROBLEM}",
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
            error = f"agent timed out after {timeout}s"
            try:
                patch = salvage_timeout_patch(
                    container,
                    repo_dir,
                    instance.get("base_commit"),
                    REPO_RUNTIME_DIFF_EXCLUDES.get(instance.get("repo"), ()),
                )
                timeout_patch_salvage = "succeeded"
            except Exception as salvage_error:  # noqa: BLE001
                timeout_patch_salvage = "failed"
                error += f"; {TIMEOUT_SALVAGE_FAILED}: {salvage_error}"

        (artifacts_dir / "agent_stdout.txt").write_text(stdout or "", encoding="utf-8")
        (artifacts_dir / "agent_stderr.txt").write_text(stderr or "", encoding="utf-8")

        if not timed_out:
            agent_out = parse_agent_stdout(stdout or "")
            agent_error = str(agent_out.get("error") or "").strip()
            if returncode != 0 or agent_error or not agent_out:
                detail = (
                    agent_error
                    or (stderr or "").strip()[-2000:]
                    or (stdout or "").strip()[-2000:]
                    or "no agent output"
                )
                raise RuntimeError(f"agent failed (returncode={returncode}): {detail}")

            patch = capture_candidate_patch(
                container,
                repo_dir,
                instance.get("base_commit"),
                REPO_RUNTIME_DIFF_EXCLUDES.get(instance.get("repo"), ()),
            )
            require_candidate_patch(patch)
    except Exception as failure:  # noqa: BLE001
        error = error or str(failure)
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
        "timeout_patch_salvage": timeout_patch_salvage,
        "error": error,
        "artifact_exports": artifact_exports,
        "prompt_protocol_version": protocol["version"],
        "prompt_fields": protocol["fields"],
        "prompt_sha256": task_prompt_sha256,
        "resource_policy": resource_policy,
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
            "\nGrade Pro through the resource-safe Scale harness launcher:\n"
            f"  uv run python {PROJECT_ROOT / 'eval/run_pro_grader.py'} \\\n"
            "    --grader-script /path/to/SWE-bench_Pro-os/swe_bench_pro_eval.py \\\n"
            "    --raw_sample_path /path/to/sample.jsonl \\\n"
            f"    --patch_path {predictions_path.with_name('predictions-pro.json')} \\\n"
            "    --output_dir /path/to/official-grade \\\n"
            "    --dockerhub_username jefzda \\\n"
            "    --scripts_dir /path/to/SWE-bench_Pro-os/scripts/run_scripts \\\n"
            "    --use_local_docker --block_network"
        )


def write_run_checkpoint(
    out_dir: Path,
    *,
    difficulty: str,
    dataset: str,
    split: str,
    workers: int,
    indexed_results: list[tuple[int, dict, dict]],
) -> None:
    """Atomically persist every completed result so interrupted runs are recoverable."""
    ordered = sorted(indexed_results, key=lambda item: item[0])
    predictions = [item[1] for item in ordered]
    summaries = [item[2] for item in ordered]

    predictions_path = out_dir / "predictions.jsonl"
    predictions_tmp = predictions_path.with_suffix(".jsonl.tmp")
    with predictions_tmp.open("w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(json.dumps(prediction) + "\n")
    predictions_tmp.replace(predictions_path)

    # Scale's Pro harness consumes a JSON array with patch/prefix field names.
    if difficulty == "pro":
        pro_path = out_dir / "predictions-pro.json"
        pro_tmp = pro_path.with_suffix(".json.tmp")
        pro_tmp.write_text(
            json.dumps(
                [
                    {
                        "instance_id": prediction["instance_id"],
                        "patch": prediction["model_patch"],
                        "prefix": MODEL_NAME,
                    }
                    for prediction in predictions
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        pro_tmp.replace(pro_path)

    summary_path = out_dir / "run_summary.json"
    summary_tmp = summary_path.with_suffix(".json.tmp")
    summary_tmp.write_text(
        json.dumps(
            {
                "difficulty": difficulty,
                "dataset": dataset,
                "split": split,
                "workers": workers,
                "prompt_protocol": prompt_protocol(difficulty),
                "completed": len(summaries),
                "summaries": summaries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_tmp.replace(summary_path)


def load_resumable_results(
    out_dir: Path,
    *,
    instances: list[dict],
    difficulty: str,
    dataset: str,
    split: str,
) -> list[tuple[int, dict, dict]]:
    """Load valid eval outcomes; only missing/infrastructure failures remain pending."""
    predictions_path = out_dir / "predictions.jsonl"
    summary_path = out_dir / "run_summary.json"
    if not predictions_path.is_file() or not summary_path.is_file():
        return []

    run = json.loads(summary_path.read_text(encoding="utf-8"))
    identity = (run.get("difficulty"), run.get("dataset"), run.get("split"))
    expected = (difficulty, dataset, split)
    if identity != expected:
        raise RuntimeError(
            f"cannot resume checkpoint for {identity}; current run is {expected}"
        )
    if run.get("prompt_protocol") != prompt_protocol(difficulty):
        # Legacy Pro checkpoints omitted two required fields. Reusing even one
        # such row would silently mix incompatible input protocols.
        return []

    predictions = [
        json.loads(line)
        for line in predictions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    prediction_by_id = {row.get("instance_id"): row for row in predictions}
    summary_by_id = {
        row.get("instance_id"): row for row in (run.get("summaries") or [])
    }
    if len(prediction_by_id) != len(predictions):
        raise RuntimeError("cannot resume checkpoint with duplicate prediction IDs")
    if len(summary_by_id) != len(run.get("summaries") or []):
        raise RuntimeError("cannot resume checkpoint with duplicate summary IDs")

    resumable = []
    for index, instance in enumerate(instances, start=1):
        instance_id = instance["instance_id"]
        prediction = prediction_by_id.get(instance_id)
        summary = summary_by_id.get(instance_id)
        if prediction is None or summary is None:
            continue
        if summary.get("prompt_sha256") != prompt_sha256(
            instance, difficulty=difficulty
        ):
            continue
        model_patch = prediction.get("model_patch")
        error = str(summary.get("error") or "").strip()
        valid_timeout = (
            bool(summary.get("timed_out"))
            and error.startswith("agent timed out after ")
            and summary.get("timeout_patch_salvage") == "succeeded"
            and TIMEOUT_SALVAGE_FAILED not in error
        )
        valid_completion = (
            summary.get("returncode") == 0
            and not summary.get("timed_out")
            and not error
            and isinstance(model_patch, str)
            and bool(model_patch.strip())
        )
        if (
            (valid_completion or valid_timeout)
            and isinstance(model_patch, str)
        ):
            prediction = dict(prediction)
            prediction["model_patch"] = strip_binary_diff_hunks(
                model_patch
            )
            summary = dict(summary)
            summary["has_patch"] = bool(prediction["model_patch"].strip())
            summary["patch_chars"] = len(prediction["model_patch"])
            resumable.append((index, prediction, summary))
    return resumable


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
    parser.add_argument(
        "--model",
        default=os.environ.get("LANGBRIDGE_MODEL", ""),
        help="Force a single model for all roles (empty = eval/config.json DeepSeek stack)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Concurrent Docker workers (default: Pro=2, Verified=4).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=PUBLIC_EVAL_TIMEOUT_SECONDS,
        help="Per-instance agent timeout in seconds (default: 7200 / 2 hours).",
    )
    parser.add_argument("--out", default=str(PROJECT_ROOT / "eval" / "out"))
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse clean completions in --out and rerun missing/infra-failed instances.",
    )
    args = parser.parse_args()

    from util.eval_config import agent_defaults

    sut = agent_defaults()
    provider = os.environ.get("LANGBRIDGE_API_PROVIDER") or sut["provider"]
    try:
        api_key = load_api_key(provider)
    except (KeyboardInterrupt, EOFError):
        sys.exit("No API key available.")
    if not api_key:
        sys.exit(
            "No API key found. Set DEEPSEEK_API_KEY / MOONSHOT_API_KEY / OPENAI_API_KEY "
            "or create ~/.langbridge/config.json before running the eval."
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

    default_workers = 2 if difficulty == "pro" else 4
    workers = max(1, int(args.workers if args.workers is not None else default_workers))
    indexed_results = []
    if args.resume:
        indexed_results = load_resumable_results(
            out_dir,
            instances=instances,
            difficulty=difficulty,
            dataset=dataset,
            split=args.split,
        )
        print(
            f"Resuming {len(indexed_results)}/{len(instances)} valid outcome(s); "
            "missing and infrastructure-failed instances will run again."
        )
    resumed_ids = {
        prediction["instance_id"] for _, prediction, _ in indexed_results
    }
    print(f"Running with {workers} concurrent Docker worker(s).")

    def run_indexed(index, instance):
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
        return index, prediction, summary

    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for index, instance in enumerate(instances, start=1):
            if instance["instance_id"] in resumed_ids:
                print(f"[resume {index}/{len(instances)}] {instance['instance_id']}")
                continue
            print(f"[queue {index}/{len(instances)}] {instance['instance_id']} ({instance['repo']})")
            future = pool.submit(run_indexed, index, instance)
            futures[future] = (index, instance)

        for future in concurrent.futures.as_completed(futures):
            index, instance = futures[future]
            try:
                result = future.result()
            except Exception as failure:  # noqa: BLE001
                prediction = {
                    "instance_id": instance["instance_id"],
                    "model_name_or_path": MODEL_NAME,
                    "model_patch": "",
                }
                summary = {
                    "instance_id": instance["instance_id"],
                    "repo": instance["repo"],
                    "has_patch": False,
                    "patch_chars": 0,
                    "returncode": None,
                    "timed_out": False,
                    "error": str(failure),
                    "duration_s": 0.0,
                    "artifact_exports": {},
                }
                result = (index, prediction, summary)
            indexed_results.append(result)
            _, _, summary = result
            print(
                f"[done {index}/{len(instances)}] {summary['instance_id']}"
                f" -> patch: {summary['has_patch']} ({summary['patch_chars']} chars), "
                f"{summary['duration_s']}s, timed_out={summary['timed_out']}"
                + (f", error={summary['error']}" if summary["error"] else "")
            )
            if summary.get("artifact_exports"):
                print(f"  -> traces: {summary['artifact_exports']}")
            write_run_checkpoint(
                out_dir,
                difficulty=difficulty,
                dataset=dataset,
                split=args.split,
                workers=workers,
                indexed_results=indexed_results,
            )

    indexed_results.sort(key=lambda item: item[0])
    predictions = [item[1] for item in indexed_results]
    summaries = [item[2] for item in indexed_results]

    predictions_path = out_dir / "predictions.jsonl"
    write_run_checkpoint(
        out_dir,
        difficulty=difficulty,
        dataset=dataset,
        split=args.split,
        workers=workers,
        indexed_results=indexed_results,
    )

    produced = sum(1 for summary in summaries if summary["has_patch"])
    print(f"\nWrote {predictions_path}")
    print(f"Produced a patch for {produced}/{len(summaries)} instances.")
    print_grade_hint(difficulty, predictions_path)


if __name__ == "__main__":
    main()
