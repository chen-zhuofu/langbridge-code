"""Parallel Docker runner for langbridge-bench eval.

Each task runs in its own container with the repo checked out, a pytest venv,
and the langbridge agent. Tasks execute concurrently (--workers).

  uv run python evals/langbridge-bench/run_eval_docker.py --role loop --workers 4 --limit 5

Requires Docker (user in the ``docker`` group, or wrap with ``sg docker -c``).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from langbridge_code.settings import EVAL_LAYER_TIMEOUT_SECONDS, GRADE_TIMEOUT_SECONDS, load_api_key
from langbridge_code.training import langbridge_bench, metrics
from langbridge_code.training.bench import split_diff

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
SPECS_DIR = Path(langbridge_bench.SPECS_DIR)

BASE_IMAGE = os.environ.get("LANGBENCH_DOCKER_IMAGE", "python:3.12-bookworm")
CONTAINER_SRC = "/opt/langbridge/src"
CONTAINER_REPO = "/work/repo"
CONTAINER_ARTIFACTS = "/root/lb_artifacts"

ROLE_LAYER = {
    "loop": "l4",
    "l4": "l4",
    "l5": "l5",
    "pm": "pm",
}


def docker(args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def ensure_image(image):
    if docker(["image", "inspect", image]).returncode == 0:
        return
    print(f"  pulling {image} ...")
    result = docker(["pull", image])
    if result.returncode != 0:
        raise RuntimeError(f"docker pull failed for {image}: {result.stderr.strip()}")


def container_exec(name, command, env=None, timeout=None, workdir=None):
    args = ["exec"]
    if workdir:
        args += ["-w", workdir]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    args += [name, "bash", "-lc", command]
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def bootstrap_container(container):
    """git + uv + langbridge runtime deps inside the base image."""
    script = """#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v git >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq git curl ca-certificates
fi
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="/root/.local/bin:$PATH"
uv pip install --system -q httpx openai numpy prompt-toolkit textual
"""
    return _write_and_copy_script(container, script, "/tmp/lb_bootstrap.sh", timeout=900)


def _write_and_copy_script(container, script_text, remote_path, timeout=None):
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(script_text)
        local_path = handle.name
    try:
        docker(["cp", local_path, f"{container}:{remote_path}"])
        return container_exec(container, f"bash {remote_path}", timeout=timeout)
    finally:
        os.unlink(local_path)


def setup_repo(container, spec):
    """Shallow checkout + pytest venv (same recipe as reference_test.py)."""
    repo = spec["repo"]
    base = spec["base_commit"]
    script = f"""#!/bin/bash
set -euo pipefail
export PATH="/root/.local/bin:$PATH"
rm -rf {CONTAINER_REPO}
mkdir -p {CONTAINER_REPO}
cd {CONTAINER_REPO}
git init -q
git remote add origin https://github.com/{repo}.git
git fetch -q --depth 1 origin {base}
git checkout -q FETCH_HEAD
export SETUPTOOLS_SCM_PRETEND_VERSION=9999.0.0
uv venv .refvenv
PY=.refvenv/bin/python
uv pip install -q --python "$PY" -e . pytest || uv pip install -q --python "$PY" . pytest
"""
    return _write_and_copy_script(container, script, "/tmp/lb_setup_repo.sh", timeout=1200)


def run_agent(container, spec, layer, api_env, model, timeout):
    task = spec["problem_statement"].replace("'", "'\"'\"'")
    env = {
        **api_env,
        "PYTHONPATH": CONTAINER_SRC,
        "LANGBRIDGE_AGENT_STATE_DIR": CONTAINER_ARTIFACTS,
        "LANGBRIDGE_LAYER": layer,
        "LANGBRIDGE_TASK": spec["problem_statement"],
    }
    if model:
        env["LANGBRIDGE_MODEL"] = model
    cmd = f"python3 -m langbridge_code.training.evals._run_layer"
    try:
        result = container_exec(container, cmd, env=env, timeout=timeout, workdir=CONTAINER_REPO)
        timed_out = False
    except subprocess.TimeoutExpired as expired:
        result = expired
        timed_out = True
    return result, timed_out


def capture_diff(container):
    cmd = f"cd {CONTAINER_REPO} && git add -A -- ':!.refvenv' && git diff --cached -- ':!.refvenv'"
    result = container_exec(container, cmd)
    return split_diff(result.stdout or "")


def parse_agent_stdout(stdout):
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def run_one_spec(spec, artifacts_root, api_env, model, role, timeout, grade):
    task_id = spec["task_id"]
    layer = ROLE_LAYER[role]
    container = f"langbridge-lbench-{task_id}".replace("__", "_").replace("/", "-")[:63]
    artifacts_dir = artifacts_root / task_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    error = ""
    timed_out = False
    agent_out = {}
    diff = ""
    gt_pass = False
    grade_status = "not_graded"

    docker(["rm", "-f", container])
    try:
        ensure_image(BASE_IMAGE)
        started_container = docker(
            ["run", "-d", "--name", container, BASE_IMAGE, "sleep", "infinity"]
        )
        if started_container.returncode != 0:
            raise RuntimeError(f"docker run failed: {started_container.stderr.strip()}")

        bootstrap = bootstrap_container(container)
        (artifacts_dir / "bootstrap.txt").write_text(
            (bootstrap.stdout or "") + (bootstrap.stderr or ""), encoding="utf-8"
        )
        if bootstrap.returncode != 0:
            raise RuntimeError("container bootstrap failed; see bootstrap.txt")

        container_exec(container, f"mkdir -p {CONTAINER_SRC} {CONTAINER_ARTIFACTS}")
        copy = docker(["cp", f"{SRC_PATH}/.", f"{container}:{CONTAINER_SRC}"])
        if copy.returncode != 0:
            raise RuntimeError(f"docker cp src failed: {copy.stderr.strip()}")

        setup = setup_repo(container, spec)
        (artifacts_dir / "setup.txt").write_text(
            (setup.stdout or "") + (setup.stderr or ""), encoding="utf-8"
        )
        if setup.returncode != 0:
            raise RuntimeError("repo setup failed; see setup.txt")

        result, timed_out = run_agent(container, spec, layer, api_env, model, timeout)
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        (artifacts_dir / "agent_stdout.txt").write_text(stdout, encoding="utf-8")
        (artifacts_dir / "agent_stderr.txt").write_text(stderr, encoding="utf-8")
        agent_out = parse_agent_stdout(stdout)
        diff = capture_diff(container)

        graded = grade(task_id, diff)
        gt_pass = bool(graded.get("resolved"))
        grade_status = graded.get("status", "graded")
    except Exception as failure:  # noqa: BLE001
        error = str(failure)
    finally:
        docker(["rm", "-f", container])

    duration = round(time.time() - started, 1)
    approved = bool(agent_out.get("approved"))
    if role == "pm":
        approved = bool(agent_out.get("completed"))

    summary = {
        "task_id": task_id,
        "repo": spec["repo"],
        "role": role,
        "layer": layer,
        "duration_s": duration,
        "approved": approved,
        "gt_pass": gt_pass,
        "grade_status": grade_status,
        "diff_chars": len(diff),
        "timed_out": timed_out,
        "error": error,
    }
    row = _row_from_summary(summary, role, diff=diff)
    return summary, row


def _patch_lines(diff):
    return sum(
        1
        for line in (diff or "").splitlines()
        if (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++", "---"))
    )


def _row_from_summary(summary, role, diff=""):
    task_id = summary["task_id"]
    if role == "loop":
        return {
            "task_id": task_id,
            "rounds": 1,
            "approved": summary["approved"],
            "gt_pass": summary["gt_pass"],
            "pushed_back": False,
            "jury_convened": False,
            "responsiveness": None,
            "alignment": None,
        }
    if role in ("l4", "l5"):
        return {
            "task_id": task_id,
            "gt_pass": summary["gt_pass"],
            "turns": None,
            "patch_lines": _patch_lines(diff),
            "grade_status": summary["grade_status"],
        }
    if role == "pm":
        return {
            "task_id": task_id,
            "gt_pass": summary["gt_pass"],
            "completed": summary["approved"],
            "component_tasks": None,
            "pm_rounds": None,
            "l5_fraction": None,
            "grade_status": summary["grade_status"],
        }
    raise ValueError(f"unsupported role {role}")


def _api_env():
    env = {}
    for key in ("MOONSHOT_API_KEY", "OPENAI_API_KEY", "LANGBRIDGE_MODEL"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    # load_api_key side effect: ensure host has a key before launching workers
    if not env.get("MOONSHOT_API_KEY") and not env.get("OPENAI_API_KEY"):
        key = load_api_key()
        provider = os.environ.get("LANGBRIDGE_API_PROVIDER", "moonshot")
        if provider == "moonshot":
            env["MOONSHOT_API_KEY"] = key
        else:
            env["OPENAI_API_KEY"] = key
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", default="loop", choices=sorted(ROLE_LAYER))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default=os.environ.get("LANGBRIDGE_MODEL", ""))
    parser.add_argument("--timeout", type=int, default=EVAL_LAYER_TIMEOUT_SECONDS)
    parser.add_argument("--grade-timeout", type=int, default=GRADE_TIMEOUT_SECONDS)
    parser.add_argument("--out", default=str(PROJECT_ROOT / "evals" / "langbridge-bench" / "out"))
    args = parser.parse_args()

    specs = langbridge_bench.specs()
    if not specs:
        sys.exit(f"No specs found under {SPECS_DIR}")
    if args.limit:
        specs = specs[: args.limit]

    api_env = _api_env()
    out_dir = Path(args.out)
    artifacts_root = out_dir / "docker_artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    ws = langbridge_bench.Workspaces()
    grade = langbridge_bench.make_grader(ws, timeout=args.grade_timeout)

    print(f"Running {len(specs)} tasks with {args.workers} Docker workers (role={args.role}).")

    summaries = []
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_one_spec,
                spec,
                artifacts_root,
                api_env,
                args.model,
                args.role,
                args.timeout,
                grade,
            ): spec
            for spec in specs
        }
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            try:
                summary, row = future.result()
            except Exception as failure:  # noqa: BLE001
                summary = {
                    "task_id": spec["task_id"],
                    "error": str(failure),
                    "gt_pass": False,
                    "approved": False,
                    "duration_s": 0,
                }
                row = _row_from_summary(
                    {**summary, "grade_status": "error", "diff_chars": 0, "timed_out": False},
                    args.role,
                    diff="",
                )
            summaries.append(summary)
            rows.append(row)
            print(
                f"  {summary['task_id']}: gt_pass={summary.get('gt_pass')} "
                f"approved={summary.get('approved')} "
                f"{summary.get('duration_s', 0)}s"
                + (f" error={summary['error']}" if summary.get("error") else "")
            )

    run_summary = {
        "role": args.role,
        "workers": args.workers,
        "model": args.model or os.environ.get("LANGBRIDGE_MODEL"),
        "summaries": summaries,
    }
    (out_dir / "docker_run_summary.json").write_text(
        json.dumps(run_summary, indent=2), encoding="utf-8"
    )

    metric_rows = rows
    computed = metrics.compute_metrics(args.role, metric_rows)
    path = metrics.record_result(args.role, metric_rows, model=args.model or None, dataset="langbridge-bench-docker")
    metrics.write_leaderboard()
    print(f"\nmetrics: {computed}")
    print(f"recorded: {path}")
    print(f"artifacts: {artifacts_root}")


if __name__ == "__main__":
    main()
