"""Append-only trace of coder↔reviewer handoffs for offline optimizer."""
import json
from datetime import datetime, timezone
from pathlib import Path

from langbridge_cli.settings import OPTIMIZER_TRACE_DIR


def trace_path(run_log_path) -> Path:
    if run_log_path is None:
        run_log_path = "session"
    stem = Path(str(run_log_path)).stem
    OPTIMIZER_TRACE_DIR.mkdir(parents=True, exist_ok=True)
    return OPTIMIZER_TRACE_DIR / f"{stem}.jsonl"


def append_event(run_log_path, event: dict) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    path = trace_path(run_log_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_events(run_log_path=None) -> list[dict]:
    path = trace_path(run_log_path)
    if not path.is_file():
        return []
    events = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def trace_to_loop_rounds(run_log_path, final_diff: str) -> dict:
    """Reconstruct coarse training rounds from optimizer trace JSONL."""
    rounds = []
    for event in read_events(run_log_path):
        if event.get("event") != "reviewer_turn":
            continue
        report = event.get("report", "")
        approved = "REVIEW_VERDICT: PASS" in report
        rounds.append(
            {
                "round": len(rounds) + 1,
                "diff": final_diff,
                "approved": approved,
                "verdict": "pass" if approved else "needs_work",
                "comments": str(report)[:4000],
                "pushed_back": False,
            }
        )
    return {"rounds": rounds, "pushed_back": False, "jury_convened": False}


def trace_to_loop_rounds_from_path(trace_file: str, final_diff: str) -> dict:
    """Parse a trace file path (used by eval subprocess parent)."""
    if not trace_file or not Path(trace_file).is_file():
        return {"rounds": [], "pushed_back": False, "jury_convened": False}
    events = []
    with open(trace_file, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    rounds = []
    for event in events:
        if event.get("event") != "reviewer_turn":
            continue
        report = event.get("report", "")
        approved = "REVIEW_VERDICT: PASS" in report
        rounds.append(
            {
                "round": len(rounds) + 1,
                "diff": final_diff,
                "approved": approved,
                "verdict": "pass" if approved else "needs_work",
                "comments": str(report)[:4000],
                "pushed_back": False,
            }
        )
    return {"rounds": rounds, "pushed_back": False, "jury_convened": False}
