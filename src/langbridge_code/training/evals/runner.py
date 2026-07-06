"""runner.py — pure eval orchestration over injected agent callables.

Each function turns a list of specs/cases into per-item rows (the schema
metrics.compute_metrics expects). The agent behaviour is injected:

  coder_fn(spec)  -> {"diff": str, "turns": int, "subtasks": int?, "subtasks_done": int?}
  review_fn(case) -> {"approved": bool}          # case carries gt_pass already
  loop_fn(spec)   -> trace (see signals.py schema), incl. "final_diff"
  pm_fn(spec)     -> {"completed": bool, "diff": str, "component_tasks": int,
                      "pm_rounds": int, "l5_fraction": float}

  grade(task_id, diff) -> {"resolved": bool, ...}   (from bench.make_git_grader)

Keeping these injectable means the same runner is used both in tests (stubs) and
in production (agents_adapter callables).
"""
from langbridge_code.training import signals


def _patch_lines(diff):
    return sum(1 for ln in (diff or "").splitlines()
               if (ln.startswith("+") or ln.startswith("-"))
               and not ln.startswith(("+++", "---")))


def eval_l4(specs, *, coder_fn, grade):
    """L4 alone implements each (normal) component_task; tests decide pass/fail."""
    rows = []
    for spec in specs:
        out = coder_fn(spec)
        diff = out.get("diff", "")
        g = grade(spec["task_id"], diff)
        rows.append({
            "task_id": spec["task_id"],
            "gt_pass": bool(g.get("resolved")),
            "turns": out.get("turns"),
            "patch_lines": _patch_lines(diff),
            "grade_status": g.get("status"),
        })
    return rows


def eval_l5(specs, *, coder_fn, grade):
    """L5 alone delivers each HARD component_task by divide-and-conquer."""
    rows = []
    for spec in specs:
        out = coder_fn(spec)
        diff = out.get("diff", "")
        g = grade(spec["task_id"], diff)
        rows.append({
            "task_id": spec["task_id"],
            "gt_pass": bool(g.get("resolved")),
            "turns": out.get("turns"),
            "patch_lines": _patch_lines(diff),
            "subtasks": out.get("subtasks"),
            "subtasks_done": out.get("subtasks_done"),
            "grade_status": g.get("status"),
        })
    return rows


def eval_l3(cases, *, review_fn):
    """L3 alone judges (task, diff) cases; truth is each case's test-based label."""
    rows = []
    for case in cases:
        out = review_fn(case)
        rows.append({
            "task_id": case.get("task_id"),
            "case": case.get("case"),
            "approved": bool(out.get("approved")),
            "gt_pass": bool(case.get("gt_pass")),
        })
    return rows


def eval_loop(specs, *, loop_fn, grade, judge=None):
    """The full inner review loop (L4<->L3 or L5<->L3). Returns (rows, traces).

    The grade is computed offline AFTER the loop and folded into trace['labels'],
    so it is never visible to the agents.
    """
    rows, traces = [], []
    for spec in specs:
        trace = loop_fn(spec)
        diff = trace.get("final_diff", "")
        g = grade(spec["task_id"], diff)
        gt = bool(g.get("resolved")) if g.get("status") == "graded" else None
        approved = bool(trace.get("approved"))
        if gt is not None:
            trace["labels"] = {
                "gt_pass": gt,
                "reward_hack": approved and not gt,
                "false_block": (not approved) and gt,
                "source": "tests",
            }
        traces.append(trace)
        resp = signals.responsiveness(trace)["score"]
        algn = signals.alignment(trace, judge)["score"]
        rows.append({
            "task_id": spec["task_id"],
            "rounds": len(trace.get("rounds", [])),
            "approved": approved,
            "gt_pass": bool(gt),
            "pushed_back": any(r.get("pushed_back") for r in trace.get("rounds", [])),
            "jury_convened": bool(trace.get("jury_convened")),
            "responsiveness": resp,
            "alignment": algn,
        })
    return rows, traces


def eval_pm(specs, *, pm_fn, grade):
    """The full PM turn: decompose -> route L4/L5 -> e2e. Tests decide pass/fail."""
    rows = []
    for spec in specs:
        out = pm_fn(spec)
        diff = out.get("diff", "")
        g = grade(spec["task_id"], diff)
        rows.append({
            "task_id": spec["task_id"],
            "gt_pass": bool(g.get("resolved")),
            "completed": bool(out.get("completed")),
            "component_tasks": out.get("component_tasks"),
            "pm_rounds": out.get("pm_rounds"),
            "l5_fraction": out.get("l5_fraction"),
            "grade_status": g.get("status"),
        })
    return rows
