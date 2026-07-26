"""Parallel Docker runner for langbridge-bench eval.

Each task runs in its dedicated pipeline image ``lb-task:<task_id>`` (repo +
``.refvenv`` already baked in by ``data-pipeline/env``). The host copies in
LangBridge source, runs main-agent e2e, then grades in-container. Artifacts
land under ``artifacts/evals/<run_id>/``.

  uv run python eval/langbridge-bench/run_eval.py --workers 4 --limit 5
  uv run python eval/langbridge-bench/run_eval.py --offset 3 --limit 2
  uv run python eval/langbridge-bench/run_eval.py --rebuild-image --task <id>

On a TTY, stderr shows a live multi-task progress board (phase + bar per
task). Use ``--no-progress`` to fall back to one-line-per-finished-task logs.

Requires Docker (user in the ``docker`` group, or wrap with ``sg docker -c``).
Task images must exist (or rebuildable from ``data/eval/docker-images/<id>/``).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from langbridge_code.settings import EVAL_LAYER_TIMEOUT_SECONDS, GRADE_TIMEOUT_SECONDS, load_api_key
from langbridge_eval import langbridge_bench, metrics
from langbridge_eval.bench import strip_eval_noise, split_diff

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
EVAL_PKG_PATH = PROJECT_ROOT / "eval"  # parent of langbridge_eval package
SPECS_DIR = Path(langbridge_bench.SPECS_DIR)
DOCKERFILE = Path(__file__).resolve().parent / "Dockerfile"
DOCKER_IMAGES_DIR = PROJECT_ROOT / "data" / "eval" / "docker-images"

DEFAULT_BASE_IMAGE = "langbridge-bench:py312"
BASE_IMAGE = os.environ.get("LANGBENCH_DOCKER_IMAGE", DEFAULT_BASE_IMAGE)
TASK_IMAGE_PREFIX = "lb-task"
CONTAINER_SRC = "/opt/langbridge/src"
CONTAINER_EVAL = "/opt/langbridge/eval"
CONTAINER_BENCH = "/opt/langbridge/eval/langbridge-bench"
CONTAINER_REPO = "/work/repo"
CONTAINER_ARTIFACTS = "/tmp/lb_agent_state"
CONTAINER_SESSION_ARTIFACTS = "/root/lb_session_artifacts"
CONTAINER_GRADE_DIR = "/tmp/lb_grade"
CONTAINER_PYTHONPATH = f"{CONTAINER_SRC}:{CONTAINER_EVAL}"
# Task images put .refvenv/bin first on PATH (for the agent), so harness
# processes inside task containers must pin the system interpreter explicitly.
HARNESS_PYTHON = "/usr/local/bin/python3"
DEFAULT_OUT = PROJECT_ROOT / "artifacts" / "evals"

# Egress lockdown (no sudo needed, auto-provisioned per run): agent containers
# run on an --internal Docker network (no route to the outside). Their only
# egress is the lb-eval-proxy container (attached to both this network and the
# default bridge), a CONNECT proxy that allows the LLM API host only.
EVAL_NETWORK = "lb-eval-net"
PROXY_NAME = "lb-eval-proxy"
PROXY_PORT = 3128
PROXY_URL = f"http://{PROXY_NAME}:{PROXY_PORT}"
PROXY_SCRIPT = Path(__file__).resolve().parent / "egress_proxy.py"

# Pipeline stage → approximate progress fill for the live board.
_PHASE_FRAC = {
    "queued": 0.0,
    "start": 0.05,
    "bootstrap": 0.1,
    "setup": 0.2,
    "agent": 0.35,
    "diff": 0.85,
    "grade": 0.92,
    "done": 1.0,
    "error": 1.0,
}


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    mins, secs = divmod(seconds, 60)
    if mins < 60:
        return f"{mins}m{secs:02d}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins:02d}m"


def _bar(fraction: float, width: int = 12) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    return "█" * filled + "░" * (width - filled)


class EvalProgress:
    """Live multi-task progress board for Docker eval runs.

    TTY: redraws in place on stderr. Non-TTY / --no-progress: no-op (caller
    prints completion lines as before).
    """

    def __init__(
        self,
        task_ids: list[str],
        *,
        agent_timeout: float,
        enabled: bool,
        stream=None,
    ):
        self.task_ids = list(task_ids)
        self.agent_timeout = max(1.0, float(agent_timeout))
        self.enabled = bool(enabled)
        self.stream = stream or sys.stderr
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._drawn_lines = 0
        self._states: dict[str, dict] = {
            tid: {
                "phase": "queued",
                "detail": "",
                "phase_at": self._started_at,
                "started_at": None,
                "finished_at": None,
                "gt_pass": None,
                "error": "",
            }
            for tid in self.task_ids
        }

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="eval-progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._render(final=True)

    def set_phase(self, task_id: str, phase: str, detail: str = "") -> None:
        if task_id not in self._states:
            return
        now = time.time()
        with self._lock:
            state = self._states[task_id]
            if state["started_at"] is None and phase != "queued":
                state["started_at"] = now
            state["phase"] = phase
            state["detail"] = detail or ""
            state["phase_at"] = now
            if phase in ("done", "error"):
                state["finished_at"] = now

    def finish(self, task_id: str, summary: dict) -> None:
        error = (summary.get("error") or "").strip()
        phase = "error" if error else "done"
        detail = ""
        if error:
            detail = error.splitlines()[0][:60]
        elif summary.get("gt_pass") is not None:
            detail = f"gt_pass={summary.get('gt_pass')}"
        self.set_phase(task_id, phase, detail)
        with self._lock:
            state = self._states[task_id]
            state["gt_pass"] = summary.get("gt_pass")
            state["error"] = error
            if summary.get("duration_s") is not None and state["started_at"] is not None:
                # Prefer reported duration for the finished label.
                state["finished_at"] = state["started_at"] + float(summary["duration_s"])

    def _fraction(self, state: dict, now: float) -> float:
        phase = state["phase"]
        base = _PHASE_FRAC.get(phase, 0.0)
        if phase != "agent":
            return base
        started = state["phase_at"] or now
        # Smooth fill while the agent is running (caps before diff/grade).
        elapsed = max(0.0, now - started)
        agent_span = _PHASE_FRAC["diff"] - _PHASE_FRAC["agent"]
        return min(_PHASE_FRAC["diff"] - 0.01, base + agent_span * (elapsed / self.agent_timeout))

    def _lines(self) -> list[str]:
        now = time.time()
        with self._lock:
            states = {tid: dict(self._states[tid]) for tid in self.task_ids}
        done = sum(1 for s in states.values() if s["phase"] in ("done", "error"))
        passed = sum(1 for s in states.values() if s["gt_pass"] is True)
        failed = sum(
            1
            for s in states.values()
            if s["phase"] in ("done", "error") and s["gt_pass"] is not True
        )
        header = (
            f"langbridge-bench docker  {done}/{len(self.task_ids)} done  "
            f"pass={passed} fail={failed}  elapsed {_fmt_duration(now - self._started_at)}"
        )
        lines = [header, "─" * min(72, max(48, len(header)))]
        label_w = min(36, max((len(t) for t in self.task_ids), default=12))
        for tid in self.task_ids:
            state = states[tid]
            phase = state["phase"]
            frac = self._fraction(state, now)
            if state["started_at"] is None:
                elapsed = 0.0
            elif state["finished_at"] is not None:
                elapsed = state["finished_at"] - state["started_at"]
            else:
                elapsed = now - state["started_at"]
            label = tid if len(tid) <= label_w else tid[: label_w - 1] + "…"
            detail = state["detail"]
            if phase == "done" and state["gt_pass"] is True:
                phase_label = "pass"
            elif phase == "done":
                phase_label = "fail"
            else:
                phase_label = phase
            right = f"{phase_label:<9} {_fmt_duration(elapsed)}"
            if detail:
                right += f"  {detail}"
            lines.append(f"  {label:<{label_w}}  {_bar(frac)}  {right}")
        return lines

    def _render(self, *, final: bool = False) -> None:
        if not self.enabled:
            return
        lines = self._lines()
        out = self.stream
        with self._lock:
            prev = self._drawn_lines
            if prev:
                # Move to top of previous board and clear downward.
                out.write(f"\033[{prev}A")
            for line in lines:
                out.write(f"\033[2K{line}\n")
            if prev > len(lines):
                for _ in range(prev - len(lines)):
                    out.write("\033[2K\n")
                out.write(f"\033[{prev - len(lines)}A")
            out.flush()
            self._drawn_lines = 0 if final else len(lines)

    def _loop(self) -> None:
        while not self._stop.wait(0.5):
            self._render()


def docker(args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def image_exists(image: str) -> bool:
    return docker(["image", "inspect", image]).returncode == 0


def task_image_tag(spec: dict) -> str:
    """Dedicated pipeline image for this task (``lb-task:<task_id>``)."""
    explicit = (spec.get("docker_image") or "").strip()
    if explicit:
        return explicit
    return f"{TASK_IMAGE_PREFIX}:{spec['task_id']}"


def ensure_base_image(*, rebuild: bool = False) -> None:
    """Ensure shared base ``langbridge-bench:py312`` (task Dockerfiles FROM this)."""
    if not rebuild and image_exists(BASE_IMAGE):
        return
    print(f"  building base {BASE_IMAGE} from {DOCKERFILE} ...")
    result = docker(
        ["build", "-t", BASE_IMAGE, "-f", str(DOCKERFILE), str(DOCKERFILE.parent)]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {BASE_IMAGE}:\n"
            f"{(result.stdout or '')[-2000:]}\n{(result.stderr or '')[-2000:]}"
        )


def ensure_task_image(spec: dict, *, rebuild: bool = False) -> str:
    """Ensure ``lb-task:<id>`` exists; build from ``data/eval/docker-images/`` if needed."""
    tag = task_image_tag(spec)
    task_id = spec["task_id"]
    if not rebuild and image_exists(tag):
        return tag

    context = DOCKER_IMAGES_DIR / task_id
    dockerfile = context / "Dockerfile"
    if not dockerfile.is_file():
        raise RuntimeError(
            f"missing task image {tag} and no Dockerfile at {dockerfile}. "
            f"Run: uv run python data-pipeline/env/build_env.py "
            f"(or the full pipeline) for {task_id}"
        )
    ensure_base_image(rebuild=False)
    print(f"  building {tag} from {dockerfile} ...")
    result = docker(["build", "-t", tag, "-f", str(dockerfile), str(context)])
    if result.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {tag}:\n"
            f"{(result.stdout or '')[-2000:]}\n{(result.stderr or '')[-2000:]}"
        )
    return tag


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
    """Sanity-check tools already baked into the eval image."""
    script = """#!/bin/bash
set -euo pipefail
export PATH="/usr/local/bin:/root/.local/bin:$PATH"
command -v git >/dev/null
command -v curl >/dev/null
command -v gcc >/dev/null
command -v uv >/dev/null
python -c "import httpx, openai, numpy, prompt_toolkit, textual, pytest"
echo "bootstrap ok: $(uv --version) git=$(git --version) gcc=$(gcc --version | head -1)"
"""
    return _write_and_copy_script(container, script, "/tmp/lb_bootstrap.sh", timeout=60)


def _write_and_copy_script(container, script_text, remote_path, timeout=None):
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(script_text)
        local_path = handle.name
    try:
        docker(["cp", local_path, f"{container}:{remote_path}"])
        return container_exec(container, f"bash {remote_path}", timeout=timeout)
    finally:
        os.unlink(local_path)


def prepare_task_workspace(container, spec):
    """Verify baked repo/venv in the task image and reset to a clean tree."""
    base = spec["base_commit"]
    script = f"""#!/bin/bash
set -euo pipefail
export PATH="/usr/local/bin:/root/.local/bin:$PATH"
test -d {CONTAINER_REPO}/.git
test -x {CONTAINER_REPO}/.refvenv/bin/python
cd {CONTAINER_REPO}
git reset --hard {base}
git clean -fdq -e .refvenv -e .langbridge
.refvenv/bin/python -c "import pytest"
echo "task workspace ready at {CONTAINER_REPO}"
"""
    return _write_and_copy_script(container, script, "/tmp/lb_prepare_task.sh", timeout=120)


def run_agent(container, spec, api_env, model, timeout):
    env = {
        **api_env,
        "PYTHONPATH": CONTAINER_PYTHONPATH,
        "PYTHONUNBUFFERED": "1",
        "LANGBRIDGE_AGENT_STATE_DIR": CONTAINER_ARTIFACTS,
        "LANGBRIDGE_ARTIFACTS_DIR": CONTAINER_SESSION_ARTIFACTS,
        "LANGBRIDGE_TASK": spec["problem_statement"],
        # Soft budget: the agent wraps up on its own ~3 min before the hard
        # container_exec kill below, so it can emit its report/telemetry.
        "LANGBRIDGE_MAX_AGENT_SECONDS": str(max(60, int(timeout) - 180)),
    }
    if model:
        env["LANGBRIDGE_MODEL"] = model
    # Temp log only — not copied to host.
    # Avoid bash process-substitution/tee — it has killed the worker container (exit 137).
    log_path = "/tmp/lb_agent_run.log"
    cmd = (
        f"mkdir -p {CONTAINER_SESSION_ARTIFACTS} {CONTAINER_ARTIFACTS} && "
        f"{HARNESS_PYTHON} -u -m langbridge_eval.run_agent "
        f">{log_path} 2>&1; ec=$?; cat {log_path}; exit $ec"
    )
    try:
        result = container_exec(container, cmd, env=env, timeout=timeout, workdir=CONTAINER_REPO)
        timed_out = False
    except subprocess.TimeoutExpired as expired:
        timed_out = True
        logged = container_exec(container, f"cat {log_path} 2>/dev/null || true")
        result = subprocess.CompletedProcess(
            args=getattr(expired, "args", None),
            returncode=-1,
            stdout=logged.stdout or getattr(expired, "stdout", None) or "",
            stderr=getattr(expired, "stderr", None) or "",
        )
    return result, timed_out


def capture_diff(container, spec=None):
    from langbridge_eval.bench import DIFF_EXCLUDE_PATHSPECS
    from langbridge_eval.langbridge_bench import _strip_test_hunks

    excludes = " ".join(f"'{p}'" for p in DIFF_EXCLUDE_PATHSPECS)
    base = (spec or {}).get("base_commit") or "HEAD"
    # Diff index against base_commit so agent commits are included (not just vs HEAD).
    cmd = (
        f"cd {CONTAINER_REPO} && git add -A -- {excludes} "
        f"&& git diff {base} --cached -- {excludes}"
    )
    result = container_exec(container, cmd)
    diff = split_diff(strip_eval_noise(result.stdout or ""))
    test_files = (spec or {}).get("test_files") if spec else None
    return _strip_test_hunks(diff, test_files)


def parse_agent_stdout(stdout):
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def _docker_cp_out(container, remote_path, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    # docker cp to a directory path copies the basename into it; for files use dest file.
    result = docker(["cp", f"{container}:{remote_path}", str(local_path)])
    if result.returncode != 0:
        raise RuntimeError(
            f"docker cp {remote_path} failed: {(result.stderr or '').strip()}"
        )


def grade_in_container(container, spec, candidate_diff, grade_timeout):
    """Reset to base, grade inside the container, return parsed grade dict."""
    base = spec["base_commit"]
    container_exec(container, f"mkdir -p {CONTAINER_GRADE_DIR}")

    with tempfile.TemporaryDirectory(prefix="lb_docker_grade_") as tmp:
        tmp_path = Path(tmp)
        spec_path = tmp_path / "spec.json"
        diff_path = tmp_path / "candidate.diff"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        diff_path.write_text(candidate_diff or "", encoding="utf-8")
        docker(["cp", str(spec_path), f"{container}:{CONTAINER_GRADE_DIR}/spec.json"])
        docker(["cp", str(diff_path), f"{container}:{CONTAINER_GRADE_DIR}/candidate.diff"])

    reset = container_exec(
        container,
        f"cd {CONTAINER_REPO} && git reset --hard {base} && git clean -fdq "
        f"-e .refvenv -e .langbridge",
    )
    if reset.returncode != 0:
        raise RuntimeError(
            f"reset to base_commit failed: {(reset.stderr or reset.stdout or '').strip()}"
        )

    env = {"PYTHONPATH": CONTAINER_PYTHONPATH}
    cmd = (
        f"{HARNESS_PYTHON} -m langbridge_eval.grade_checkout "
        f"--spec {CONTAINER_GRADE_DIR}/spec.json "
        f"--diff {CONTAINER_GRADE_DIR}/candidate.diff "
        f"--out {CONTAINER_GRADE_DIR}/grade.json "
        f"--repo {CONTAINER_REPO} "
        f"--timeout {int(grade_timeout)}"
    )
    result = container_exec(
        container, cmd, env=env, timeout=grade_timeout + 120, workdir=CONTAINER_REPO
    )
    grade_log = (result.stdout or "") + (result.stderr or "")
    with tempfile.TemporaryDirectory(prefix="lb_grade_out_") as tmp:
        local_grade = Path(tmp) / "grade.json"
        try:
            _docker_cp_out(container, f"{CONTAINER_GRADE_DIR}/grade.json", local_grade)
            graded = json.loads(local_grade.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001
            graded = {
                "resolved": False,
                "status": "grade_failed",
                "error": str(error),
                "log": grade_log[-2000:],
            }
    return graded


def _active_api_host():
    from urllib.parse import urlparse

    from langbridge_code.settings import API_BASE_URL

    host = urlparse(API_BASE_URL).hostname or ""
    if not host:
        sys.exit("Cannot determine LLM API host from settings.API_BASE_URL.")
    return host


def _ensure_internal_network():
    """Create the --internal eval network; recreate if a non-internal one exists."""
    inspect = docker(["network", "inspect", "-f", "{{.Internal}}", EVAL_NETWORK])
    if inspect.returncode == 0:
        if (inspect.stdout or "").strip() == "true":
            return
        removed = docker(["network", "rm", EVAL_NETWORK])
        if removed.returncode != 0:
            sys.exit(
                f"Docker network {EVAL_NETWORK!r} exists but is not internal, and "
                f"could not be removed (containers attached?): {removed.stderr.strip()}"
            )
    created = docker(["network", "create", "--internal", EVAL_NETWORK])
    if created.returncode != 0:
        sys.exit(f"docker network create {EVAL_NETWORK} failed: {created.stderr.strip()}")


def ensure_egress_guard(image):
    """Start the allowlist proxy; return docker args for agent containers.

    The proxy container joins both the internal network (reachable by agents)
    and the default bridge (has internet). Everything is provisioned with
    plain docker commands — no root/iptables on the host.
    """
    api_host = _active_api_host()
    _ensure_internal_network()

    docker(["rm", "-f", PROXY_NAME])
    created = docker(
        ["create", "--name", PROXY_NAME, "--restart", "unless-stopped",
         "-e", f"ALLOWED_HOSTS={api_host}", "-e", f"PORT={PROXY_PORT}",
         image, "python3", "-u", "/egress_proxy.py"]
    )
    if created.returncode != 0:
        sys.exit(f"docker create {PROXY_NAME} failed: {created.stderr.strip()}")
    copied = docker(["cp", str(PROXY_SCRIPT), f"{PROXY_NAME}:/egress_proxy.py"])
    if copied.returncode != 0:
        sys.exit(f"docker cp egress_proxy.py failed: {copied.stderr.strip()}")
    connected = docker(["network", "connect", EVAL_NETWORK, PROXY_NAME])
    if connected.returncode != 0:
        sys.exit(f"docker network connect failed: {connected.stderr.strip()}")
    started = docker(["start", PROXY_NAME])
    if started.returncode != 0:
        sys.exit(f"docker start {PROXY_NAME} failed: {started.stderr.strip()}")

    return ["--network", EVAL_NETWORK], api_host


def verify_network_lockdown(image, net_args, api_host):
    """Canary from the agent network: API via proxy works, everything else doesn't."""

    def probe(cmd):
        return docker(["run", "--rm", *net_args, image, "bash", "-c", cmd]).returncode == 0

    if probe("timeout 5 bash -c '</dev/tcp/github.com/443'"):
        sys.exit(
            f"Network guard check failed: direct github.com:443 IS reachable from "
            f"{EVAL_NETWORK} — the network is not internal."
        )
    if not probe(
        f"curl -s -o /dev/null --connect-timeout 8 -x {PROXY_URL} https://{api_host}/"
    ):
        sys.exit(
            f"Network guard check failed: {api_host} unreachable via {PROXY_NAME}.\n"
            f"Check: docker logs {PROXY_NAME}"
        )
    if probe(
        f"curl -s -o /dev/null --connect-timeout 8 -x {PROXY_URL} https://github.com/"
    ):
        sys.exit(
            f"Network guard check failed: proxy tunnelled github.com — allowlist broken."
        )


def run_one_spec(spec, artifacts_root, api_env, model, timeout, grade_timeout, progress=None, net_args=()):
    task_id = spec["task_id"]

    def phase(name, detail=""):
        if progress is not None:
            progress.set_phase(task_id, name, detail)

    container = f"langbridge-lbench-{task_id}".replace("__", "_").replace("/", "-")[:63]
    artifacts_dir = artifacts_root / task_id
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # Live-bind session artifacts so progress/traces appear on the host while
    # the agent runs (and survive mid-run interrupts).
    session_dir = artifacts_dir / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    error = ""
    timed_out = False
    agent_out = {}
    diff = ""
    gt_pass = False
    grade_status = "not_graded"
    graded = {}
    telemetry = {}

    phase("start")
    docker(["rm", "-f", container])
    image = task_image_tag(spec)
    try:
        # Image should already be ensured in main(); re-check for clear errors.
        if not image_exists(image):
            image = ensure_task_image(spec, rebuild=False)

        started_container = docker(
            [
                "run",
                "-d",
                "--name",
                container,
                "-v",
                f"{session_dir.resolve()}:{CONTAINER_SESSION_ARTIFACTS}",
                *net_args,
                image,
                "sleep",
                "infinity",
            ]
        )
        if started_container.returncode != 0:
            raise RuntimeError(f"docker run failed: {started_container.stderr.strip()}")

        phase("bootstrap")
        bootstrap = bootstrap_container(container)
        if bootstrap.returncode != 0:
            detail = ((bootstrap.stdout or "") + (bootstrap.stderr or "")).strip()[-2000:]
            raise RuntimeError(f"container bootstrap failed: {detail}")

        container_exec(
            container,
            f"mkdir -p {CONTAINER_SRC} {CONTAINER_EVAL} {CONTAINER_BENCH} "
            f"{CONTAINER_ARTIFACTS} {CONTAINER_SESSION_ARTIFACTS}",
        )
        copy = docker(["cp", f"{SRC_PATH}/.", f"{container}:{CONTAINER_SRC}"])
        if copy.returncode != 0:
            raise RuntimeError(f"docker cp src failed: {copy.stderr.strip()}")
        copy_eval = docker(
            ["cp", f"{EVAL_PKG_PATH}/langbridge_eval", f"{container}:{CONTAINER_EVAL}/"]
        )
        if copy_eval.returncode != 0:
            raise RuntimeError(f"docker cp langbridge_eval failed: {copy_eval.stderr.strip()}")

        phase("setup", image)
        setup = prepare_task_workspace(container, spec)
        if setup.returncode != 0:
            detail = ((setup.stdout or "") + (setup.stderr or "")).strip()[-2000:]
            raise RuntimeError(f"task workspace prepare failed: {detail}")

        phase("agent")
        result, timed_out = run_agent(container, spec, api_env, model, timeout)
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        agent_out = parse_agent_stdout(stdout)
        if not agent_out and stderr:
            agent_out = {"report": stderr[-2000:]}
        telemetry = dict(agent_out.get("telemetry") or {})
        # Ensure the sleep-infinity worker is still up before mutating the tree.
        status = docker(
            ["inspect", "-f", "{{.State.Running}} {{.State.OOMKilled}} {{.State.ExitCode}}", container]
        )
        if (status.stdout or "").strip().split(" ", 1)[0].lower() != "true":
            raise RuntimeError(
                f"container not running after agent: {(status.stdout or status.stderr or '').strip()}"
            )
        phase("diff")
        diff = capture_diff(container, spec)
        (artifacts_dir / "candidate.diff").write_text(diff, encoding="utf-8")

        # Session notes are bind-mounted to session_dir for the whole run — no
        # post-agent docker cp needed.

        phase("grade")
        graded = grade_in_container(container, spec, diff, grade_timeout)
        gt_pass = bool(graded.get("resolved"))
        grade_status = graded.get("status", "graded")
        (artifacts_dir / "grade.json").write_text(
            json.dumps(graded, indent=2), encoding="utf-8"
        )
    except Exception as failure:  # noqa: BLE001
        error = str(failure)
        phase("error", error.splitlines()[0][:60])
    finally:
        docker(["rm", "-f", container])

    duration = round(time.time() - started, 1)

    summary = {
        "task_id": task_id,
        "repo": spec["repo"],
        "docker_image": image,
        "duration_s": duration,
        "gt_pass": gt_pass,
        "grade_status": grade_status,
        "diff_chars": len(diff),
        "timed_out": timed_out,
        "error": error,
        "artifacts_dir": str(artifacts_dir),
    }
    (artifacts_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    aggregates = dict(telemetry.get("aggregates") or {})
    if graded.get("test_latency_s") is not None:
        aggregates["total_test_latency_s"] = graded["test_latency_s"]
    telemetry["aggregates"] = aggregates

    row = {
        "task_id": task_id,
        "gt_pass": gt_pass,
        "grade_status": grade_status,
        "telemetry": telemetry,
        "dimensions": {
            "static_analysis": graded.get("static_analysis"),
        },
    }
    return summary, row


def _api_env():
    """Pass provider + matching API key into the container."""
    from langbridge_code.settings import _PROVIDER_ENV, active_api_provider

    env = {}
    for key in (
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "LANGBRIDGE_MODEL",
        "LANGBRIDGE_API_PROVIDER",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value

    provider = env.get("LANGBRIDGE_API_PROVIDER") or active_api_provider()
    env["LANGBRIDGE_API_PROVIDER"] = provider

    env_names = _PROVIDER_ENV.get(provider, ())
    if env_names and not any(env.get(name) for name in env_names):
        env[env_names[0]] = load_api_key(provider)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0, help="skip the first N specs")
    parser.add_argument("--task", default=None, help="run only this task_id")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default=os.environ.get("LANGBRIDGE_MODEL", ""))
    parser.add_argument("--timeout", type=int, default=EVAL_LAYER_TIMEOUT_SECONDS)
    parser.add_argument("--grade-timeout", type=int, default=GRADE_TIMEOUT_SECONDS)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--rebuild-image",
        action="store_true",
        help="force rebuild of each selected lb-task:<id> from data/eval/docker-images/",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable live multi-task progress board (always off when stderr is not a TTY)",
    )
    parser.add_argument(
        "--open-network",
        action="store_true",
        help="run containers on the default network with full internet access "
        "(debugging only — results are not benchmark-valid)",
    )
    args = parser.parse_args()

    specs = langbridge_bench.specs()
    if not specs:
        sys.exit(f"No specs found under {SPECS_DIR}")
    if args.task:
        matched = [s for s in specs if s["task_id"] == args.task]
        if not matched:
            known = ", ".join(s["task_id"] for s in specs[:8])
            sys.exit(f"No spec with task_id={args.task!r}. Examples: {known}...")
        specs = matched
    if args.offset:
        specs = specs[args.offset :]
    if args.limit:
        specs = specs[: args.limit]
    if not specs:
        sys.exit("No specs left after --task/--offset/--limit filters.")

    print(f"Ensuring {len(specs)} dedicated task image(s) ...")
    for spec in specs:
        tag = ensure_task_image(spec, rebuild=args.rebuild_image)
        print(f"  {spec['task_id']} -> {tag}")

    api_env = _api_env()
    if args.open_network:
        net_args = []
        print("WARNING: --open-network — containers get unrestricted internet access.")
    else:
        guard_image = task_image_tag(specs[0])
        net_args, api_host = ensure_egress_guard(guard_image)
        verify_network_lockdown(guard_image, net_args, api_host)
        # httpx (OpenAI SDK) honors proxy env vars; read_webpage/curl go the
        # same way and get 403'd by the allowlist.
        api_env["HTTPS_PROXY"] = api_env["HTTP_PROXY"] = PROXY_URL
        api_env["NO_PROXY"] = "localhost,127.0.0.1"
        print(f"Network guard: {EVAL_NETWORK} (egress via {PROXY_NAME} to {api_host} only)")
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / run_id
    artifacts_root = out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    # Keep metrics JSON next to this run's task dirs.
    os.environ["LANGBRIDGE_EVAL_RESULTS_DIR"] = str(out_dir / "results")

    print(
        f"Running {len(specs)} tasks with {args.workers} Docker workers "
        f"(dedicated lb-task images, in-container grade)."
    )

    progress = EvalProgress(
        [spec["task_id"] for spec in specs],
        agent_timeout=args.timeout,
        enabled=sys.stderr.isatty() and not args.no_progress,
    )

    summaries = []
    rows = []
    progress.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    run_one_spec,
                    spec,
                    artifacts_root,
                    api_env,
                    args.model,
                    args.timeout,
                    args.grade_timeout,
                    progress,
                    net_args,
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
                        "duration_s": 0,
                        "grade_status": "error",
                    }
                    row = {
                        "task_id": spec["task_id"],
                        "gt_pass": False,
                        "grade_status": "error",
                    }
                progress.finish(summary["task_id"], summary)
                summaries.append(summary)
                rows.append(row)
                if not progress.enabled:
                    print(
                        f"  {summary['task_id']}: gt_pass={summary.get('gt_pass')} "
                        f"{summary.get('duration_s', 0)}s"
                        + (f" error={summary['error']}" if summary.get("error") else "")
                    )
    finally:
        progress.stop()

    run_summary = {
        "workers": args.workers,
        "model": args.model or os.environ.get("LANGBRIDGE_MODEL"),
        "grade": "in_container",
        "network": "open" if args.open_network else EVAL_NETWORK,
        "summaries": summaries,
    }
    (out_dir / "docker_run_summary.json").write_text(
        json.dumps(run_summary, indent=2), encoding="utf-8"
    )

    computed = metrics.compute_metrics("e2e", rows)
    path = metrics.record_result(
        "e2e",
        rows,
        model=args.model or "docker",
        dataset="langbridge-bench-docker",
    )
    detail_path = path[:-5] + "-detail.json" if path.endswith(".json") else path + "-detail"
    print(f"\nmetrics: {computed}")
    print(f"recorded: {path}")
    print(f"detail: {detail_path}")
    print(f"run_summary: {out_dir / 'docker_run_summary.json'}")
    print(f"artifacts: {artifacts_root}")


if __name__ == "__main__":
    main()
