"""agents_adapter.py — drive the REAL L3/L4/L5/PM agents for eval/evolver.

Builds the injectable callables the runners/evolver expect (coder_fn, review_fn,
loop_fn, pm_fn) by, for each task:

  1. creating a fresh git worktree of the target repo at the task's base_commit,
  2. running one agent layer in a subprocess (cwd = that worktree, so the tools are
     sandboxed to it) via langbridge_cli.training.evals._run_layer,
  3. capturing the candidate's source changes with `git diff`,
  4. (for loop_fn) reconstructing a coarse trace from the shared L4/L3 worklog.

This is the integration seam that depends on the chosen target repo + model. The
scoring/evolver logic around it is model-agnostic and unit-tested separately.

NOTE: full per-round diffs are not recovered from the worklog, so responsiveness/
alignment from the subprocess path are approximate; the worklog gives the per-round
verdicts/comments/push-backs/jury, which is enough for calibration + patterns. A
future improvement is to have the loop emit a structured trace directly.
"""
import json
import os
import subprocess
import sys
import tempfile

from langbridge_cli.training import bench

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


def _run_layer(worktree, layer, task, context="", model=None, timeout=1800):
    scratch = tempfile.mkdtemp(prefix="lb_eval_state_")
    extra = {"LANGBRIDGE_LAYER": layer, "LANGBRIDGE_TASK": task,
             "LANGBRIDGE_CONTEXT": context}
    if model:
        extra["LANGBRIDGE_MODEL"] = model
    proc = subprocess.run(
        [sys.executable, "-m", "langbridge_cli.training.evals._run_layer"],
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


# --------------------------------------------------------------------------- #
# Worklog -> coarse trace reconstruction (for loop_fn).                        #
# --------------------------------------------------------------------------- #
def _parse_worklog(path, final_diff):
    rounds = []
    pushed_back = jury = False
    if not path or not os.path.exists(path):
        return {"rounds": rounds, "pushed_back": pushed_back, "jury_convened": jury}
    text = open(path).read()
    for chunk in text.split("### ")[1:]:
        head, _, body = chunk.partition("\n")
        role = head.strip()
        token = ""
        for ln in body.splitlines():
            if ln.startswith("WORKLOG_TOKEN:"):
                token = ln.split(":", 1)[1].strip().lower()
        if "Dispute jury" in role:
            jury = True
        if token == "push back":
            pushed_back = True
        if role.startswith("L3"):
            approved = token == "pass"
            rounds.append({
                "round": len(rounds) + 1,
                "diff": final_diff,
                "approved": approved,
                "verdict": "pass" if approved else "needs_work",
                "comments": body.strip(),
                "pushed_back": False,
            })
    return {"rounds": rounds, "pushed_back": pushed_back, "jury_convened": jury}


# --------------------------------------------------------------------------- #
# The injectable callables.                                                    #
# --------------------------------------------------------------------------- #
def make_callables(repo=None, model=None, timeout=1800):
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
            # Stage the case's diff so L3 reviews a real tree.
            bench._apply(wt, case.get("diff", ""))
            out = _run_layer(wt, "l3", case["problem_statement"],
                             context=f"A change was made:\n{case.get('diff','')[:4000]}",
                             model=model, timeout=timeout)
            return {"approved": bool(out.get("approved"))}
        finally:
            bench._remove_worktree(repo, wt)

    def loop_fn(spec, layer="l4"):
        wt = _worktree(spec)
        try:
            out = _run_layer(wt, layer, spec["problem_statement"], model=model, timeout=timeout)
            final_diff = _capture_diff(wt)
            parsed = _parse_worklog(out.get("shared_worklog", ""), final_diff)
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
            out = _run_layer(wt, "pm", spec["problem_statement"], model=model, timeout=timeout)
            return {"completed": bool(out.get("completed")), "diff": _capture_diff(wt),
                    "component_tasks": None, "pm_rounds": None, "l5_fraction": None}
        finally:
            bench._remove_worktree(repo, wt)

    return {"coder_fn": coder_fn, "l5_fn": l5_fn, "review_fn": review_fn,
            "loop_fn": loop_fn, "pm_fn": pm_fn}
