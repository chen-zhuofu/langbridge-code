"""langbridge_bench.py — eval/train on the self-built langbridge-bench dataset.

Instances live as one JSON per task under evals/langbridge-bench/instances/.
Eval-ready specs are persisted under evals/langbridge-bench/specs/ (built by
evals/langbridge-bench/materialize.py from validated jsonl).

Grading reuses reference_test.py (uv venv + pytest, same F2P/P2P logic as the
dataset pipeline).
"""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from langbridge_cli.settings import EVAL_LAYER_TIMEOUT_SECONDS, GRADE_TIMEOUT_SECONDS
from langbridge_cli.training import bench

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|conftest\.py$",
    re.IGNORECASE,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = str(_REPO_ROOT / "src")
_BENCH_DIR = _REPO_ROOT / "evals" / "langbridge-bench"
INSTANCES_DIR = os.environ.get("LANGBRIDGE_INSTANCES_DIR", str(_BENCH_DIR / "instances"))
SPECS_DIR = os.environ.get("LANGBRIDGE_SPECS_DIR", str(_BENCH_DIR / "specs"))


def _ref():
    path = _BENCH_DIR / "reference_test.py"
    spec = importlib.util.spec_from_file_location("_lb_reference_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strip_test_hunks(diff, test_files=None):
    test_files = set(test_files or [])
    out, keep = [], True
    for line in (diff or "").splitlines(keepends=True):
        if line.startswith("diff --git "):
            m = re.search(r" b/(\S+)", line)
            path = m.group(1) if m else ""
            keep = not (path in test_files or _TEST_PATH_RE.search(path))
        if keep:
            out.append(line)
    return "".join(out)


def specs(hard=None, directory=None):
    return bench.list_specs(directory=directory or SPECS_DIR, ok_only=True, hard=hard)


class Workspaces:
    """Builds and caches a runnable checkout+venv per instance, and resets it."""

    def __init__(self, root=None):
        self.ref = _ref()
        self.root = Path(root or os.environ.get("LANGBRIDGE_BENCH_WORK")
                         or tempfile.mkdtemp(prefix="lb_langbridge_bench_"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._ready = {}

    def prepare(self, spec):
        tid = spec["task_id"]
        if tid in self._ready:
            return self._ready[tid]
        repo_dir = self.root / tid
        if not (repo_dir / ".git").exists():
            self.ref.shallow_checkout(spec["repo"], spec["base_commit"], repo_dir)
        py = self.ref.make_venv(repo_dir)
        subprocess.run(["uv", "pip", "install", "--python", str(py), "openai"],
                       cwd=repo_dir, capture_output=True, text=True, env=self.ref.BUILD_ENV)
        self._ready[tid] = (str(repo_dir), str(py))
        return self._ready[tid]

    def reset(self, repo_dir):
        # capture_diff() stages via `git add`; reset must drop the index too.
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo_dir, capture_output=True, text=True)
        subprocess.run(["git", "clean", "-fdq", "-e", ".refvenv"], cwd=repo_dir,
                       capture_output=True, text=True)

    def capture_diff(self, repo_dir, test_files=None):
        subprocess.run(["git", "add", "-A", "--", ":!.refvenv"], cwd=repo_dir,
                       capture_output=True, text=True)
        out = subprocess.run(["git", "diff", "--cached", "--", ":!.refvenv"],
                             cwd=repo_dir, capture_output=True, text=True).stdout
        return _strip_test_hunks(out, test_files)


def make_grader(workspaces, specs_directory=None, timeout=GRADE_TIMEOUT_SECONDS):
    ref = _ref()
    directory = specs_directory or SPECS_DIR
    by_id = {s["task_id"]: s for s in bench.list_specs(directory=directory, ok_only=True)}

    def grade(task_id, candidate_diff):
        spec = by_id.get(task_id)
        if spec is None:
            return {"resolved": False, "status": "no_spec"}
        repo_dir, py = workspaces.prepare(spec)
        workspaces.reset(repo_dir)
        ok, err = ref.apply_patch(repo_dir, spec["test_patch"])
        if not ok:
            return {"resolved": False, "status": f"test_patch_apply_failed: {err}"}
        cand = bench.split_diff(candidate_diff)
        if cand.strip():
            ok, err = ref.apply_patch(repo_dir, cand)
            if not ok:
                return {"resolved": False, "status": f"candidate_patch_apply_failed: {err}"}
        outcomes = ref.run_pytest(py, repo_dir, spec["test_files"], timeout)
        f2p = spec["fail_to_pass"]
        p2p = spec["pass_to_pass"]
        f2p_passed = sum(1 for t in f2p if outcomes.get(t) == "PASSED")
        regressions = [t for t in p2p if t in outcomes and outcomes.get(t) != "PASSED"]
        resolved = bool(f2p) and f2p_passed == len(f2p) and not regressions
        workspaces.reset(repo_dir)
        return {"resolved": resolved, "f2p_passed": f2p_passed, "f2p_total": len(f2p),
                "regressions": regressions, "status": "graded"}

    return grade


def _run_layer(py, repo_dir, layer, task, context="", model=None, timeout=EVAL_LAYER_TIMEOUT_SECONDS):
    scratch = tempfile.mkdtemp(prefix="lb_eval_state_")
    env = dict(os.environ)
    env.update({
        "LANGBRIDGE_AGENT_STATE_DIR": scratch,
        "PYTHONPATH": _SRC + os.pathsep + env.get("PYTHONPATH", ""),
        "LANGBRIDGE_LAYER": layer,
        "LANGBRIDGE_TASK": task,
        "LANGBRIDGE_CONTEXT": context,
    })
    if model:
        env["LANGBRIDGE_MODEL"] = model
    proc = subprocess.run(
        [py, "-m", "langbridge_cli.training.evals._run_layer"],
        cwd=repo_dir, env=env, capture_output=True, text=True, timeout=timeout,
    )
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"report": proc.stderr[-2000:], "approved": False, "completed": False}


def make_callables(workspaces, model=None, timeout=EVAL_LAYER_TIMEOUT_SECONDS):
    from langbridge_cli.training.evals.agents_adapter import _parse_worklog

    def _agent(spec, layer):
        repo_dir, py = workspaces.prepare(spec)
        workspaces.reset(repo_dir)
        out = _run_layer(py, repo_dir, layer, spec["problem_statement"],
                         model=model, timeout=timeout)
        diff = workspaces.capture_diff(repo_dir)
        worklog = _parse_worklog(out.get("shared_worklog", ""), diff)
        workspaces.reset(repo_dir)
        return out, diff, worklog

    def coder_fn(spec):
        out, diff, _ = _agent(spec, "l4")
        return {"diff": diff, "turns": None, "report": out.get("report", "")}

    def l5_fn(spec):
        out, diff, _ = _agent(spec, "l5")
        return {"diff": diff, "turns": None, "report": out.get("report", "")}

    def loop_fn(spec, layer="l4"):
        out, diff, worklog = _agent(spec, layer)
        return {
            "task": spec["problem_statement"], "worker": layer,
            "rounds": worklog["rounds"] or [
                {"round": 1, "diff": diff, "approved": bool(out.get("approved")),
                 "verdict": "pass" if out.get("approved") else "needs_work",
                 "comments": "", "pushed_back": False}],
            "approved": bool(out.get("approved")),
            "jury_convened": worklog["jury_convened"], "jury_pass": None,
            "final_diff": diff,
        }

    def review_fn(case):
        repo_dir, py = workspaces.prepare(case)
        workspaces.reset(repo_dir)
        ref = _ref()
        if case.get("test_patch"):
            ok, _ = ref.apply_patch(repo_dir, case["test_patch"])
            if not ok:
                workspaces.reset(repo_dir)
                return {"approved": False}
        diff = case.get("diff", "")
        if diff.strip():
            ok, _ = ref.apply_patch(repo_dir, diff)
            if not ok:
                workspaces.reset(repo_dir)
                return {"approved": False}
        out = _run_layer(py, repo_dir, "l3", case["problem_statement"],
                         context="A change was made; verify it.", model=model, timeout=timeout)
        workspaces.reset(repo_dir)
        return {"approved": bool(out.get("approved"))}

    def pm_fn(spec):
        out, diff, _ = _agent(spec, "pm")
        return {"completed": bool(out.get("completed")), "diff": diff,
                "component_tasks": None, "pm_rounds": None, "l5_fraction": None}

    return {"coder_fn": coder_fn, "l5_fn": l5_fn, "loop_fn": loop_fn,
            "review_fn": review_fn, "pm_fn": pm_fn}
