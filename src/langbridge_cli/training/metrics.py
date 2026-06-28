"""metrics.py — score and record the five evals in one place.

We score each role separately so co-optimization is visible: a gain on one side
shouldn't be paid for by a hidden regression in another. The five eval types:

  l4   : L4 alone implements a normal component_task -> graded by hidden tests.
  l5   : L5 alone implements a HARD component_task by divide-and-conquer -> tests.
  l3   : L3 alone judges (task, diff) cases -> agreement with the test-based label.
  loop : the full L4<->L3 (or L5<->L3) inner review loop -> tests + loop quality.
  pm   : the full PM turn (decompose -> route L4/L5 -> e2e) -> tests + plan/route.

Ground truth is always the hidden regression tests for a task (see bench.py),
computed offline and never shown to the agents. A runner collects per-item rows
and calls record_result(); aggregates are computed here so every run is scored
identically and comparably across epochs/checkpoints.
"""
import datetime
import json
import os
from pathlib import Path
from typing import Optional

EVAL_TYPES = ("l4", "l5", "l3", "pm", "loop")


def results_dir() -> str:
    env = os.environ.get("LANGBRIDGE_EVAL_RESULTS_DIR")
    if env:
        return os.path.abspath(env)
    repo_root = Path(__file__).resolve().parents[3]
    return str(repo_root / "training" / "results")


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def _mean(values) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _coder_metrics(rows: list) -> dict:
    """Shared by l4 and l5 (both implement and are graded by tests)."""
    n = len(rows)
    gt_pass = sum(1 for r in rows if r.get("gt_pass"))
    empty = sum(1 for r in rows if (r.get("patch_lines") or 0) == 0)
    out = {
        "n": n,
        "gt_pass_rate": _rate(gt_pass, n),
        "avg_turns": _mean([r.get("turns") for r in rows]),
        "avg_patch_lines": _mean([r.get("patch_lines") for r in rows]),
        "empty_patch_rate": _rate(empty, n),
    }
    # L5 reports how many technical_sub_tasks it planned/finished.
    if any("subtasks" in r for r in rows):
        out["avg_subtasks"] = _mean([r.get("subtasks") for r in rows])
        out["avg_subtasks_done"] = _mean([r.get("subtasks_done") for r in rows])
    return out


def _reviewer_metrics(rows: list) -> dict:
    """L3: treat REVIEW_VERDICT PASS as predicting 'this patch is correct'.
    Ground truth is gt_pass."""
    n = len(rows)
    tp = sum(1 for r in rows if r.get("approved") and r.get("gt_pass"))
    fp = sum(1 for r in rows if r.get("approved") and not r.get("gt_pass"))
    fn = sum(1 for r in rows if not r.get("approved") and r.get("gt_pass"))
    tn = sum(1 for r in rows if not r.get("approved") and not r.get("gt_pass"))
    precision = _rate(tp, tp + fp)
    recall = _rate(tp, tp + fn)
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 3)
    return {
        "n": n,
        "accuracy": _rate(tp + tn, n),
        # false approval = passed broken code (too lenient -> reward-hacking risk)
        "false_approval_rate": _rate(fp, fp + tn),
        # false rejection = blocked good code (too strict / nitpicky)
        "false_rejection_rate": _rate(fn, fn + tp),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def _loop_metrics(rows: list) -> dict:
    """Inner L4<->L3 / L5<->L3 loop."""
    n = len(rows)
    gt_pass = sum(1 for r in rows if r.get("gt_pass"))
    approved = sum(1 for r in rows if r.get("approved"))
    first_pass = sum(1 for r in rows if r.get("approved") and r.get("rounds") == 1)
    reward_hacked = sum(1 for r in rows if r.get("approved") and not r.get("gt_pass"))
    false_block = sum(1 for r in rows if not r.get("approved") and r.get("gt_pass"))
    pushed_back = sum(1 for r in rows if r.get("pushed_back"))
    juries = sum(1 for r in rows if r.get("jury_convened"))
    return {
        "n": n,
        "gt_pass_rate": _rate(gt_pass, n),
        "approval_rate": _rate(approved, n),
        "first_pass_rate": _rate(first_pass, n),
        "avg_rounds": _mean([r.get("rounds") for r in rows]),
        # reward hacking: L3 passed it, but the hidden tests fail.
        "reward_hack_rate": _rate(reward_hacked, n),
        # false block: hidden tests pass, but L3 never passed it.
        "false_block_rate": _rate(false_block, n),
        "push_back_rate": _rate(pushed_back, n),
        "jury_rate": _rate(juries, n),
        "avg_responsiveness": _mean([r.get("responsiveness") for r in rows]),
        "avg_alignment": _mean([r.get("alignment") for r in rows]),
    }


def _pm_metrics(rows: list) -> dict:
    """Top-level PM turn: did the whole user_task land?"""
    n = len(rows)
    gt_pass = sum(1 for r in rows if r.get("gt_pass"))
    completed = sum(1 for r in rows if r.get("completed"))  # PM reported BUG_STATUS: NONE
    # reward hack at the PM level: PM declared done, but hidden tests fail.
    reward_hacked = sum(1 for r in rows if r.get("completed") and not r.get("gt_pass"))
    return {
        "n": n,
        "gt_pass_rate": _rate(gt_pass, n),
        "completion_rate": _rate(completed, n),
        "reward_hack_rate": _rate(reward_hacked, n),
        "avg_component_tasks": _mean([r.get("component_tasks") for r in rows]),
        "avg_pm_rounds": _mean([r.get("pm_rounds") for r in rows]),
        "l5_routing_rate": _mean([r.get("l5_fraction") for r in rows]),
    }


_DISPATCH = {
    "l4": _coder_metrics,
    "l5": _coder_metrics,
    "l3": _reviewer_metrics,
    "loop": _loop_metrics,
    "pm": _pm_metrics,
}


def compute_metrics(eval_type: str, rows: list) -> dict:
    if eval_type not in EVAL_TYPES:
        raise ValueError(f"Unknown eval_type {eval_type!r}; expected one of {EVAL_TYPES}")
    return _DISPATCH[eval_type](rows)


def make_run_id(eval_type: str, model: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_model = (model or "model").replace("/", "-").replace(" ", "-")
    return f"{stamp}-{eval_type}-{safe_model}"


def record_result(
    eval_type: str,
    rows: list,
    *,
    model: str,
    dataset: str = "",
    config: Optional[dict] = None,
    policy_version: Optional[int] = None,
    notes: str = "",
    run_id: Optional[str] = None,
) -> str:
    """Write one eval run to <results_dir>/<eval_type>/<run_id>.json; return path."""
    if eval_type not in EVAL_TYPES:
        raise ValueError(f"Unknown eval_type {eval_type!r}; expected one of {EVAL_TYPES}")
    run_id = run_id or make_run_id(eval_type, model)
    record = {
        "eval": eval_type,
        "run_id": run_id,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "dataset": dataset,
        "policy_version": policy_version,
        "config": config or {},
        "metrics": compute_metrics(eval_type, rows),
        "per_item": rows,
        "notes": notes,
    }
    out_dir = os.path.join(results_dir(), eval_type)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{run_id}.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return path


def load_results(eval_type: str) -> list:
    out_dir = os.path.join(results_dir(), eval_type)
    if not os.path.isdir(out_dir):
        return []
    records = []
    for fname in sorted(os.listdir(out_dir)):
        if fname.endswith(".json"):
            with open(os.path.join(out_dir, fname)) as f:
                records.append(json.load(f))
    return records


# --------------------------------------------------------------------------- #
# Leaderboard report.                                                          #
# --------------------------------------------------------------------------- #
_COLUMNS = {
    "l4": ["gt_pass_rate", "avg_turns", "avg_patch_lines", "empty_patch_rate"],
    "l5": ["gt_pass_rate", "avg_subtasks", "avg_turns", "empty_patch_rate"],
    "l3": ["accuracy", "false_approval_rate", "false_rejection_rate", "f1"],
    "loop": ["gt_pass_rate", "approval_rate", "first_pass_rate", "avg_rounds",
             "reward_hack_rate", "false_block_rate"],
    "pm": ["gt_pass_rate", "completion_rate", "reward_hack_rate",
           "avg_component_tasks", "l5_routing_rate"],
}


def build_leaderboard() -> str:
    """Render a markdown leaderboard across every recorded run, newest last."""
    lines = ["# Eval leaderboard", ""]
    for et in EVAL_TYPES:
        runs = load_results(et)
        if not runs:
            continue
        cols = _COLUMNS[et]
        lines.append(f"## {et}")
        lines.append("")
        header = ["run_id", "model", "policy_v", "n"] + cols
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for run in runs:
            m = run.get("metrics", {})
            row = [
                run.get("run_id", ""),
                str(run.get("model", "")),
                str(run.get("policy_version", "")),
                str(m.get("n", "")),
            ] + [_fmt(m.get(c)) for c in cols]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    return "\n".join(lines)


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def write_leaderboard() -> str:
    path = os.path.join(results_dir(), "leaderboard.md")
    os.makedirs(results_dir(), exist_ok=True)
    with open(path, "w") as f:
        f.write(build_leaderboard())
    return path
