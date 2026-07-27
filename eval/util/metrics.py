"""metrics.py — score and record evals in one place.

Eval type:

  e2e : full main-agent turn -> hidden tests + quality dimensions.

Ground truth is always the hidden regression tests for a task (see bench.py),
computed offline and never shown to the agents.
"""
import datetime
import json
import os
from pathlib import Path
from typing import Optional

EVAL_TYPES = ("e2e",)


def results_dir() -> str:
    env = os.environ.get("LANGBRIDGE_EVAL_RESULTS_DIR")
    if env:
        return os.path.abspath(env)
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "artifacts" / "evals")


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def _e2e_metrics(rows: list) -> dict:
    n = len(rows)
    gt_pass = sum(1 for r in rows if r.get("gt_pass"))
    quality_vals = [r.get("quality_score") for r in rows if r.get("quality_score") is not None]
    return {
        "n": n,
        "gt_pass_rate": _rate(gt_pass, n),
        "avg_quality_score": round(sum(quality_vals) / len(quality_vals), 3) if quality_vals else None,
    }


_DISPATCH = {
    "e2e": _e2e_metrics,
}


def compute_metrics(eval_type: str, rows: list) -> dict:
    if eval_type not in EVAL_TYPES:
        raise ValueError(f"Unknown eval_type {eval_type!r}; expected one of {EVAL_TYPES}")
    return _DISPATCH[eval_type](rows)


def make_run_id(eval_type: str, model: str) -> str:
    """Return ``YYYYMMDD-HHMMSS-report`` (seconds precision; model/type not in name)."""
    del eval_type, model  # kept for call-site compatibility
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-report"


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
    """Write report + detail JSON under results_dir; return the report path.

    - ``<run_id>.json`` — full run record; telemetry events are aggregates only.
    - ``<run_id>-detail.json`` — per-task model/tool groups with every call.
    """
    from util.telemetry import split_report_and_detail_telemetry

    if eval_type not in EVAL_TYPES:
        raise ValueError(f"Unknown eval_type {eval_type!r}; expected one of {EVAL_TYPES}")
    run_id = run_id or make_run_id(eval_type, model)

    report_rows = []
    detail_items = []
    for row in rows:
        row = dict(row)
        telemetry = row.get("telemetry")
        report_tel, detail_events = split_report_and_detail_telemetry(telemetry)
        if telemetry is not None:
            row["telemetry"] = report_tel
        report_rows.append(row)
        if detail_events:
            detail_items.append(
                {
                    "task_id": row.get("task_id"),
                    "events": detail_events,
                }
            )

    record = {
        "eval": eval_type,
        "run_id": run_id,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "dataset": dataset,
        "policy_version": policy_version,
        "config": config or {},
        "metrics": compute_metrics(eval_type, report_rows),
        "per_item": report_rows,
        "notes": notes,
    }
    out_dir = results_dir()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{run_id}.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)

    detail_path = os.path.join(out_dir, f"{run_id}-detail.json")
    with open(detail_path, "w") as f:
        json.dump({"run_id": run_id, "per_item": detail_items}, f, indent=2)

    return path
