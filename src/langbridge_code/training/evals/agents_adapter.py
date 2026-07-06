"""agents_adapter.py — drive the REAL workflow agents for eval/evolver.

Builds injectable callables (coder_fn, review_fn, loop_fn, pm_fn) by:

  1. creating a fresh git worktree of the target repo at the task's base_commit,
  2. running one agent layer in a subprocess (cwd = that worktree),
  3. capturing the candidate's source changes with `git diff`,
  4. (for loop_fn) reconstructing a coarse trace from the optimizer JSONL trace.

NOTE: per-round diffs in the trace are the final diff repeated; verdicts and
comments come from reviewer_turn events in the optimizer trace.
"""
import json
import os
import subprocess
import sys
import tempfile

from langbridge_code.settings import EVAL_LAYER_TIMEOUT_SECONDS
from langbridge_code.training import bench
from langbridge_code.workflow.optimizer_trace import trace_to_loop_rounds_from_path

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _env(scratch, extra=None):
    env = dict(os.environ)
    env["LANGBRIDGE_AGENT_STATE_DIR"] = scratch
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    if extra:
        env.update(extra)
    return env


def _capture_diff(worktree):
    subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True, text=True)
    out = subprocess.run(["git", "diff", "--cached"], cwd=worktree,
                         capture_output=True, text=True).stdout
    return bench.split_diff(out)  # drop any test-file hunks the agent added


def _run_layer(worktree, layer, task, context="", model=None, timeout=EVAL_LAYER_TIMEOUT_SECONDS):
    scratch = tempfile.mkdtemp(prefix="lb_eval_state_")
    extra = {"LANGBRIDGE_LAYER": layer, "LANGBRIDGE_TASK": task,
             "LANGBRIDGE_CONTEXT": context}
    if model:
        extra["LANGBRIDGE_MODEL"] = model
    proc = subprocess.run(
        [sys.executable, "-m", "langbridge_code.training.evals._run_layer"],
        cwd=worktree, env=_env(scratch, extra), capture_output=True, text=True,
        timeout=timeout,
    )
    parsed = {}
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    parsed.setdefault("report", proc.stderr[-2000:] if proc.returncode else "")
    return parsed


def _parse_trace(out, final_diff):
    trace_file = out.get("optimizer_trace") or out.get("shared_worklog") or ""
    return trace_to_loop_rounds_from_path(trace_file, final_diff)


# Legacy name used by langbridge_bench.
_parse_worklog = _parse_trace


def make_callables(repo=None, model=None, timeout=EVAL_LAYER_TIMEOUT_SECONDS):
    repo = repo or bench.TARGET_REPO

    def _worktree(spec):
        return bench._make_worktree(repo, spec["base_commit"])

    def coder_fn(spec, layer="l4"):
        wt = _worktree(spec)
        try:
            out = _run_layer(wt, layer, spec["problem_statement"], model=model, timeout=timeout)
            return {"diff": _capture_diff(wt), "turns": None,
                    "report": out.get("report", "")}
        finally:
            bench._remove_worktree(repo, wt)

    def l5_fn(spec):
        return coder_fn(spec, layer="l5")

    def review_fn(case):
        wt = bench._make_worktree(repo, case["base_commit"])
        try:
            if case.get("test_patch"):
                if not bench._apply(wt, case["test_patch"]):
                    return {"approved": False}
            diff = case.get("diff", "")
            if diff.strip() and not bench._apply(wt, diff):
                return {"approved": False}
            out = _run_layer(wt, "l3", case["problem_statement"],
                             context=f"A change was made:\n{diff[:4000]}",
                             model=model, timeout=timeout)
            return {"approved": bool(out.get("approved"))}
        finally:
            bench._remove_worktree(repo, wt)

    def loop_fn(spec, layer="l4"):
        wt = _worktree(spec)
        try:
            out = _run_layer(wt, layer, spec["problem_statement"], model=model, timeout=timeout)
            final_diff = _capture_diff(wt)
            parsed = _parse_trace(out, final_diff)
            return {
                "task": spec["problem_statement"], "worker": layer,
                "rounds": parsed["rounds"] or [
                    {"round": 1, "diff": final_diff, "approved": bool(out.get("approved")),
                     "verdict": "pass" if out.get("approved") else "needs_work",
                     "comments": "", "pushed_back": False}
                ],
                "approved": bool(out.get("approved")),
                "jury_convened": parsed["jury_convened"],
                "jury_pass": None,
                "final_diff": final_diff,
            }
        finally:
            bench._remove_worktree(repo, wt)

    def pm_fn(spec):
        wt = _worktree(spec)
        try:
            out = _run_layer(wt, "workflow", spec["problem_statement"], model=model, timeout=timeout)
            return {"completed": bool(out.get("completed")), "diff": _capture_diff(wt),
                    "component_tasks": None, "pm_rounds": None, "l5_fraction": None}
        finally:
            bench._remove_worktree(repo, wt)

    return {"coder_fn": coder_fn, "l5_fn": l5_fn, "review_fn": review_fn,
            "loop_fn": loop_fn, "pm_fn": pm_fn}
