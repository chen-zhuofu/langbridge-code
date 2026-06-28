"""swebench.py — use the SWE-bench-style pytest dataset built under evals/dataset/.

"His" pipeline (evals/dataset/) collected real merged pytest-dev/pytest PRs into
SWE-bench-schema instances and validated them by running the tests pre/post fix to
fill FAIL_TO_PASS / PASS_TO_PASS. This module plugs that dataset into our eval +
evolver: it loads the validated instances as specs and grades a CANDIDATE patch the
exact same way his reference_test grades the gold patch (same venv build, same
pytest invocation, same fail->pass logic), so the judge is identical.

Why a separate module from bench.py: pytest can't be graded by a plain
`python -m pytest` in a bare checkout — it needs a uv venv, a pretend
setuptools-scm version, the pytester plugin and minversion=0. All of that already
lives in evals/dataset/reference_test.py, so we reuse it instead of duplicating.

A prepared workspace (shallow checkout at base_commit + a venv with the project,
pytest, and openai installed) is built once per instance and reused for both
running the agent and grading, resetting the tree between uses.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from langbridge_cli.training import bench

# Tests this dataset's repos keep under testing/ or tests/ (pytest uses testing/).
# A candidate patch must never ship its own tests, so we strip those hunks before
# grading or capturing the diff.
_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|conftest\.py$",
    re.IGNORECASE,
)


def _strip_test_hunks(diff, test_files=None):
    """Drop per-file sections whose path is a known test file or matches a test
    path pattern. Keeps the candidate's source changes only."""
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

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = str(_REPO_ROOT / "src")
_DATASET_DIR = _REPO_ROOT / "evals" / "dataset"
DEFAULT_DATASET = os.environ.get(
    "LANGBRIDGE_DATASET", str(_DATASET_DIR / "sample_validated.jsonl")
)


def _ref():
    """Load evals/dataset/reference_test.py as a module (it is not a package)."""
    path = _DATASET_DIR / "reference_test.py"
    spec = importlib.util.spec_from_file_location("_lb_reference_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Dataset -> specs.                                                            #
# --------------------------------------------------------------------------- #
def load_instances(path=None):
    path = path or DEFAULT_DATASET
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def instance_to_spec(inst):
    ref = _ref()
    return {
        "task_id": inst["instance_id"],
        "status": "ok" if inst.get("FAIL_TO_PASS") else "no_f2p",
        "repo": inst["repo"],
        "base_commit": inst["base_commit"],
        "problem_statement": inst.get("problem_statement", ""),
        "test_files": ref.test_files_in_patch(inst.get("test_patch", "")),
        "test_patch": inst.get("test_patch", ""),
        "gold_code_patch": inst.get("patch", ""),
        "fail_to_pass": inst.get("FAIL_TO_PASS", []),
        "pass_to_pass": inst.get("PASS_TO_PASS", []),
        "hard": bool(inst.get("hard")) or len(inst.get("FAIL_TO_PASS", [])) >= 2,
    }


def specs(path=None, hard=None):
    out = [instance_to_spec(i) for i in load_instances(path)]
    out = [s for s in out if s["status"] == "ok"]
    if hard is not None:
        out = [s for s in out if bool(s["hard"]) == hard]
    return out


# --------------------------------------------------------------------------- #
# Prepared workspaces (checkout + venv), cached per instance.                  #
# --------------------------------------------------------------------------- #
class Workspaces:
    """Builds and caches a runnable checkout+venv per instance, and resets it."""

    def __init__(self, root=None):
        self.ref = _ref()
        self.root = Path(root or os.environ.get("LANGBRIDGE_SWEBENCH_WORK")
                         or tempfile.mkdtemp(prefix="lb_swebench_"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._ready = {}  # instance_id -> (repo_dir, py)

    def prepare(self, spec):
        tid = spec["task_id"]
        if tid in self._ready:
            return self._ready[tid]
        repo_dir = self.root / tid
        if not (repo_dir / ".git").exists():
            self.ref.shallow_checkout(spec["repo"], spec["base_commit"], repo_dir)
        py = self.ref.make_venv(repo_dir)  # project (editable) + pytest
        # the agent subprocess imports langbridge_cli (via PYTHONPATH) + openai.
        subprocess.run(["uv", "pip", "install", "--python", str(py), "openai"],
                       cwd=repo_dir, capture_output=True, text=True, env=self.ref.BUILD_ENV)
        self._ready[tid] = (str(repo_dir), str(py))
        return self._ready[tid]

    def reset(self, repo_dir):
        subprocess.run(["git", "checkout", "--", "."], cwd=repo_dir, capture_output=True, text=True)
        # wipe untracked agent edits but keep the venv.
        subprocess.run(["git", "clean", "-fdq", "-e", ".refvenv"], cwd=repo_dir,
                       capture_output=True, text=True)

    def capture_diff(self, repo_dir, test_files=None):
        subprocess.run(["git", "add", "-A", "--", ":!.refvenv"], cwd=repo_dir,
                       capture_output=True, text=True)
        out = subprocess.run(["git", "diff", "--cached", "--", ":!.refvenv"],
                             cwd=repo_dir, capture_output=True, text=True).stdout
        return _strip_test_hunks(out, test_files)


# --------------------------------------------------------------------------- #
# Grader.                                                                      #
# --------------------------------------------------------------------------- #
def make_grader(workspaces, dataset_path=None, timeout=600):
    ref = _ref()
    by_id = {s["task_id"]: s for s in specs(dataset_path)}

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


# --------------------------------------------------------------------------- #
# Agent-driving callables in the prepared env (cwd = checkout, venv python).   #
# --------------------------------------------------------------------------- #
def _run_layer(py, repo_dir, layer, task, context="", model=None, timeout=1800):
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


def make_callables(workspaces, model=None, timeout=1800):
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
