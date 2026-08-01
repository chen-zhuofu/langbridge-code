"""Docker-backed main-agent turns for interactive-bench.

Starts ``lb-interactive:<task_id>``, copies LangBridge into the container, and
keeps a ``MainAgentSession`` across sim turns via a small in-container state
file. After the episode, grades FAIL_TO_PASS inside the same image.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

_BENCH = Path(__file__).resolve().parents[1]
_PIPELINE = _BENCH / "data-pipeline"
import sys

if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from _lib import paths
from _lib.diff_split import code_only_for_grade
from _lib.docker_util import docker, image_exists

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
EVAL_PKG_PATH = PROJECT_ROOT / "eval"
USER_CONFIG = Path.home() / ".langbridge-code" / "config.json"

if str(EVAL_PKG_PATH) not in sys.path:
    sys.path.insert(0, str(EVAL_PKG_PATH))

CONTAINER_SRC = "/opt/langbridge/src"
CONTAINER_PKG = f"{CONTAINER_SRC}/langbridge_code"
CONTAINER_EVAL = "/opt/langbridge/eval"
CONTAINER_REPO = "/work/repo"
CONTAINER_ARTIFACTS = "/tmp/lb_agent_state"
CONTAINER_SESSION = "/root/lb_session_artifacts"
CONTAINER_IX = "/tmp/lb_ix"
CONTAINER_PYTHONPATH = f"{CONTAINER_SRC}:{CONTAINER_EVAL}"
HARNESS_PYTHON = "/usr/local/bin/python3"

# In-container turn driver (written once per session).
_TURN_SCRIPT = r'''
import json, os, sys
from pathlib import Path

STATE = Path("/tmp/lb_ix/state.json")
STATE.parent.mkdir(parents=True, exist_ok=True)

user_text = os.environ.get("LB_USER_TEXT") or ""
turn_timeout = int(os.environ.get("LANGBRIDGE_MAX_AGENT_SECONDS") or "900")

from langbridge_code import settings
from langbridge_code.settings import load_api_key
from langbridge_code.tools.common.runtime import RuntimeBootstrapError, bootstrap_runtime
from langbridge_code.util.session import create_run_log_path
from langbridge_code.agents.main_agent import MainAgentSession
from langbridge_code.prompt.system import langbridge_system_prompt
from langbridge_code.llm.parse import extract_output_text

def auto_approve(label, name, arguments):
    return True

try:
    bootstrap_runtime()
except RuntimeBootstrapError as err:
    print(json.dumps({"assistant_text": "", "done": False, "error": f"bootstrap: {err}"}))
    raise SystemExit(0)

api_key = load_api_key()
model = os.environ.get("LANGBRIDGE_MODEL") or settings.DEFAULT_MODEL

state = {"messages": None, "turn": 0, "run_log_path": None}
if STATE.exists():
    try:
        state.update(json.loads(STATE.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        pass

turn = int(state.get("turn") or 0) + 1
messages = state.get("messages")
if not messages:
    messages = [{"role": "system", "content": langbridge_system_prompt()}]

run_log_path = state.get("run_log_path")
if not run_log_path:
    run_log_path = str(create_run_log_path(user_text[:200] or "interactive-task"))

# First user turn: wrap as implement-the-fix task.
text = user_text
if turn == 1:
    wrapper = Path("/opt/langbridge/eval/prompt/task.py")
    if wrapper.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("eval_prompt_task", wrapper)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        text = mod.TASK_WRAPPER.format(issue=user_text).strip()

session = MainAgentSession(
    api_key,
    model,
    messages,
    run_log_path,
    turn,
    target=text,
    approval_callback=auto_approve,
    question_callback=lambda *_a, **_k: "continue",
)
try:
    reply = session.send(text) or ""
except Exception as err:
    from langbridge_code.llm.client import format_api_error
    print(json.dumps({
        "assistant_text": "",
        "done": False,
        "error": format_api_error(err),
        "turn": turn,
    }))
    raise SystemExit(0)

# Persist chat for the next sim turn (keep JSON-serializable message dicts).
serializable = []
for msg in session.messages:
    if not isinstance(msg, dict):
        continue
    try:
        json.dumps(msg)
    except TypeError:
        # Drop non-JSON tool payloads; keep a text stub so history still flows.
        role = msg.get("role") or "assistant"
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            serializable.append({"role": role, "content": content})
        continue
    serializable.append(msg)
STATE.write_text(json.dumps({
    "messages": serializable or messages,
    "turn": turn,
    "run_log_path": run_log_path,
}, ensure_ascii=False), encoding="utf-8")

lower = (reply or "").lower()
doneish = any(
    p in lower
    for p in (
        "all done",
        "tests pass",
        "ready for review",
        "goal achieved",
        "i've finished",
        "i have finished",
        "implementation is complete",
        "changes are complete",
        "both changes",
        "both fixes",
    )
)
# Only end the episode when the agent clearly claims completion.
# Otherwise the user-sim can reveal/steer on the next turn.
print(json.dumps({
    "assistant_text": reply,
    "done": bool(doneish),
    "turn": turn,
    "run_log_path": run_log_path,
}))
'''

_GRADE_SCRIPT = r'''
import json, os, re, subprocess, sys
from pathlib import Path

task = json.loads(Path("/tmp/lb_ix/grade_task.json").read_text())
diff = Path("/tmp/lb_ix/candidate.diff").read_text(encoding="utf-8", errors="replace")
test_patch = task.get("test_patch") or ""
test_files = [f for f in (task.get("test_files") or []) if str(f).endswith(".py")]
f2p = list(task.get("fail_to_pass") or task.get("FAIL_TO_PASS") or [])
base = (task.get("base_commit") or "HEAD").strip() or "HEAD"
timeout = int(os.environ.get("LB_GRADE_TIMEOUT", "600"))
py = Path("/work/repo/.refvenv/bin/python")
repo = Path("/work/repo")
PYTEST_LINE_RE = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR)\b")

# Official test_patch is authoritative: never re-apply agent edits to those
# paths (or any other test-looking file) during grade.
_TEST_HINTS = ("/tests/", "/test/", "/__tests__/", "/e2e_tests/", "tests/")
_TEST_NAME_SUFFIXES = (
    "_test.py", "_test.go", "_test.ts", "_test.tsx", "_test.js", "_test.jsx",
    ".test.py", ".test.ts", ".test.tsx", ".test.js", ".test.jsx",
    ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
)

def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")

def _is_test_path(path: str) -> bool:
    p = _norm(path)
    name = Path(p).name.lower()
    lower = p.lower()
    if name == "conftest.py":
        return True
    if name.startswith("test_") and name.endswith((".py", ".go", ".ts", ".js")):
        return True
    if any(name.endswith(suf) for suf in _TEST_NAME_SUFFIXES):
        return True
    return any(h in lower for h in _TEST_HINTS)

def _paths_in_patch(patch: str) -> set[str]:
    out = set()
    for line in (patch or "").splitlines():
        if line.startswith("diff --git "):
            m = re.search(r" b/(\S+)", line)
            if m:
                out.add(_norm(m.group(1)))
    return out

def strip_test_hunks(patch: str) -> str:
    protected = _paths_in_patch(test_patch) | {_norm(f) for f in test_files if f}
    out, keep = [], True
    for line in (patch or "").splitlines(keepends=True):
        if line.startswith("diff --git "):
            m = re.search(r" b/(\S+)", line)
            path = _norm(m.group(1)) if m else ""
            keep = bool(path) and path not in protected and not _is_test_path(path)
        if keep:
            out.append(line)
    return "".join(out)

def apply(patch: str, label: str) -> None:
    if not (patch or "").strip():
        return
    r = subprocess.run(
        ["git", "apply", "--whitespace=nowarn"],
        input=patch, text=True, capture_output=True, cwd=str(repo),
    )
    if r.returncode != 0:
        # Fall back to 3-way for slightly drifted contexts.
        r2 = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "--3way"],
            input=patch, text=True, capture_output=True, cwd=str(repo),
        )
        if r2.returncode != 0:
            raise SystemExit(json.dumps({
                "error": f"{label} apply failed: {(r.stderr or r2.stderr or '')[-800:]}",
                "tests_passed": False,
            }))

subprocess.check_call(["git", "reset", "--hard", base], cwd=str(repo))
subprocess.check_call(
    ["git", "clean", "-fdq", "-e", ".refvenv", "-e", ".langbridge"],
    cwd=str(repo),
)
diff = strip_test_hunks(diff)
apply(test_patch, "test_patch")
apply(diff, "candidate_diff")

env = os.environ.copy()
env["PYTHONPATH"] = str(repo) + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
for root in sorted({Path(f).parts[0] for f in test_files if Path(f).parts}):
    rp = repo / root
    if rp.is_dir():
        env["PYTHONPATH"] = str(rp.resolve()) + ":" + env["PYTHONPATH"]

args = [str(py), "-m", "pytest", "-v", "--no-header",
        "-p", "no:cacheprovider", "-o", "addopts=", *test_files]
r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=str(repo), env=env)
outcomes = {}
for line in (r.stdout + "\n" + r.stderr).splitlines():
    m = PYTEST_LINE_RE.match(line.strip())
    if m:
        outcomes[m.group(1)] = m.group(2)
passed = [t for t in f2p if outcomes.get(t) == "PASSED"]
print(json.dumps({
    "tests_passed": bool(f2p) and len(passed) == len(f2p),
    "f2p_passed": len(passed),
    "f2p_total": len(f2p),
    "outcomes": {t: outcomes.get(t) for t in f2p},
    "pytest_rc": r.returncode,
}))
'''


def _api_env() -> dict[str, str]:
    from langbridge_code.settings import (
        _PROVIDER_ENV,
        resolve_provider_api_key,
    )
    from _lib.bench_config import apply_agent_env

    env: dict[str, str] = {}
    for key in (
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "LANGBRIDGE_MODEL",
        "LANGBRIDGE_API_PROVIDER",
        "LANGBRIDGE_API_BASE_URL",
        "OPENAI_BASE_URL",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    env = apply_agent_env(env)
    for name, env_names in _PROVIDER_ENV.items():
        if any(env.get(key) for key in env_names):
            continue
        key = resolve_provider_api_key(name)
        if key:
            env[env_names[0]] = key
    return env


def _write_sut_user_config(container: str) -> None:
    """Install this bench's SUT defaults into the container user config.

    Interactive CLI config often pins moonshot/kimi; overlay forces the
    interactive-bench/config.json agent stack while keeping api_keys.
    """
    from _lib.bench_config import merge_agent_user_config

    user_cfg: dict = {}
    if USER_CONFIG.exists():
        try:
            user_cfg = json.loads(USER_CONFIG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            user_cfg = {}
    merged = merge_agent_user_config(user_cfg)
    _container_exec(container, "mkdir -p /root/.langbridge-code")
    with tempfile.TemporaryDirectory(prefix="lb-ix-cfg-") as tmp:
        path = Path(tmp) / "config.json"
        path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        docker(["cp", str(path), f"{container}:/root/.langbridge-code/config.json"])


def _container_exec(
    container: str,
    cmd: str,
    *,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    workdir: str = CONTAINER_REPO,
) -> subprocess.CompletedProcess:
    args = ["exec", "-w", workdir]
    for key, value in (env or {}).items():
        args.extend(["-e", f"{key}={value}"])
    args.extend([container, "bash", "-lc", cmd])
    return docker(args, timeout=timeout)


class DockerMainAgent:
    """Callable agent_turn backed by LangBridge main agent in Docker."""

    def __init__(
        self,
        spec: dict,
        *,
        artifacts_dir: Path,
        turn_timeout_sec: int = 900,
        model: str | None = None,
    ):
        self.spec = spec
        self.task_id = spec["task_id"]
        self.image = spec.get("docker_image") or paths.task_image(self.task_id)
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir = self.artifacts_dir / "session"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.turn_timeout_sec = int(turn_timeout_sec)
        self.model = model or os.environ.get("LANGBRIDGE_MODEL") or ""
        self.container = (
            f"lb-ix-{self.task_id}".replace("__", "_").replace("/", "-")[:60]
        )
        self.api_env = _api_env()
        if self.model:
            self.api_env["LANGBRIDGE_MODEL"] = self.model
        self._started = False
        self._closed = False
        self.last_grade: dict[str, Any] | None = None
        self.candidate_diff = ""

    def start(self) -> None:
        if self._started:
            return
        if not image_exists(self.image):
            raise RuntimeError(f"missing image {self.image}")
        docker(["rm", "-f", self.container])
        started = docker(
            [
                "run",
                "-d",
                "--name",
                self.container,
                "-v",
                f"{self.session_dir.resolve()}:{CONTAINER_SESSION}",
                self.image,
                "sleep",
                "infinity",
            ]
        )
        if started.returncode != 0:
            raise RuntimeError(f"docker run failed: {started.stderr}")

        _container_exec(
            self.container,
            f"mkdir -p {CONTAINER_PKG} {CONTAINER_EVAL} {CONTAINER_ARTIFACTS} "
            f"{CONTAINER_SESSION} {CONTAINER_IX}",
        )
        for src, dest in (
            (f"{SRC_PATH}/.", f"{self.container}:{CONTAINER_PKG}"),
            (f"{EVAL_PKG_PATH}/util", f"{self.container}:{CONTAINER_EVAL}/"),
            (f"{EVAL_PKG_PATH}/prompt", f"{self.container}:{CONTAINER_EVAL}/"),
        ):
            cp = docker(["cp", src, dest])
            if cp.returncode != 0:
                raise RuntimeError(f"docker cp failed ({src}): {cp.stderr}")

        _write_sut_user_config(self.container)

        base = self.spec.get("base_commit") or "HEAD"
        prep = _container_exec(
            self.container,
            f"cd {CONTAINER_REPO} && git reset --hard {shlex.quote(base)} && "
            f"git clean -fdq -e .refvenv -e .langbridge && "
            f"test -x .refvenv/bin/python",
            timeout=120,
        )
        if prep.returncode != 0:
            raise RuntimeError(
                f"prepare workspace failed: {(prep.stderr or prep.stdout or '')[-800:]}"
            )

        with tempfile.TemporaryDirectory(prefix="lb-ix-turn-") as tmp:
            script = Path(tmp) / "turn_runner.py"
            script.write_text(_TURN_SCRIPT, encoding="utf-8")
            docker(["cp", str(script), f"{self.container}:{CONTAINER_IX}/turn_runner.py"])

        self._started = True

    def __call__(self, user_text: str) -> dict[str, Any]:
        self.start()
        # Sim no-op ("continue") should not burn another full main-agent budget.
        if (user_text or "").strip().lower() == "continue":
            return {
                "assistant_text": "Still working on the remaining requirements.",
                "done": False,
            }
        env = {
            **self.api_env,
            "PYTHONPATH": CONTAINER_PYTHONPATH,
            "PYTHONUNBUFFERED": "1",
            "LANGBRIDGE_AGENT_STATE_DIR": CONTAINER_ARTIFACTS,
            "LANGBRIDGE_ARTIFACTS_DIR": CONTAINER_SESSION,
            "LANGBRIDGE_MAX_AGENT_SECONDS": str(max(60, self.turn_timeout_sec - 60)),
            "LB_USER_TEXT": user_text,
        }
        cmd = f"{HARNESS_PYTHON} -u {CONTAINER_IX}/turn_runner.py"
        try:
            result = _container_exec(
                self.container,
                cmd,
                env=env,
                timeout=self.turn_timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return {
                "assistant_text": "[agent turn timed out]",
                "done": True,
                "error": "turn_timeout",
            }

        payload = _parse_json_line(result.stdout or "")
        if not payload:
            err = ((result.stderr or "") + (result.stdout or ""))[-1500:]
            return {
                "assistant_text": "",
                "done": False,
                "error": err or f"turn rc={result.returncode}",
            }
        return payload

    def capture_diff(self) -> str:
        base = self.spec.get("base_commit") or "HEAD"
        result = _container_exec(
            self.container,
            f"cd {CONTAINER_REPO} && git add -A "
            f"':(exclude).langbridge' ':(exclude).refvenv' "
            f"&& git diff {shlex.quote(base)} --cached -- "
            f"':(exclude).langbridge' ':(exclude).refvenv'",
            timeout=120,
        )
        self.candidate_diff = result.stdout or ""
        (self.artifacts_dir / "candidate.diff").write_text(
            self.candidate_diff, encoding="utf-8"
        )
        return self.candidate_diff

    def grade(self, *, timeout: int = 600) -> dict[str, Any]:
        self.start()
        if not self.candidate_diff:
            self.capture_diff()
        # Keep full candidate.diff on disk for debugging; grade only code hunks
        # so the official test_patch is the sole source of eval tests.
        graded_diff = code_only_for_grade(
            self.candidate_diff,
            test_patch=self.spec.get("test_patch") or "",
            test_files=list(self.spec.get("test_files") or []),
        )
        (self.artifacts_dir / "candidate.code.diff").write_text(
            graded_diff, encoding="utf-8"
        )
        with tempfile.TemporaryDirectory(prefix="lb-ix-grade-") as tmp:
            task_path = Path(tmp) / "grade_task.json"
            diff_path = Path(tmp) / "candidate.diff"
            script = Path(tmp) / "grade_runner.py"
            task_path.write_text(json.dumps(self.spec), encoding="utf-8")
            diff_path.write_text(graded_diff, encoding="utf-8")
            script.write_text(_GRADE_SCRIPT, encoding="utf-8")
            docker(["cp", str(task_path), f"{self.container}:{CONTAINER_IX}/grade_task.json"])
            docker(["cp", str(diff_path), f"{self.container}:{CONTAINER_IX}/candidate.diff"])
            docker(["cp", str(script), f"{self.container}:{CONTAINER_IX}/grade_runner.py"])
        result = _container_exec(
            self.container,
            f"LB_GRADE_TIMEOUT={int(timeout)} {HARNESS_PYTHON} -u {CONTAINER_IX}/grade_runner.py",
            timeout=timeout + 120,
        )
        payload = _parse_json_line(result.stdout or "") or {
            "error": ((result.stderr or result.stdout or "")[-800:]),
            "tests_passed": False,
        }
        self.last_grade = payload
        (self.artifacts_dir / "grade.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        return payload

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        docker(["rm", "-f", self.container])


def _parse_json_line(stdout: str) -> dict[str, Any] | None:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


def make_docker_agent(
    spec: dict,
    *,
    artifacts_dir: Path | None = None,
    turn_timeout_sec: int = 900,
    model: str | None = None,
) -> DockerMainAgent:
    """Factory used by ``eval/run_eval.py`` (returns a callable agent_turn)."""
    out = artifacts_dir or (paths.EVAL_OUT / "live" / spec["task_id"])
    return DockerMainAgent(
        spec,
        artifacts_dir=out,
        turn_timeout_sec=turn_timeout_sec,
        model=model,
    )
