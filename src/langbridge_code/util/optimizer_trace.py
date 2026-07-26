"""Append-only human-readable coder/reviewer handoff lines for session traces."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from langbridge_code.util.artifacts import session_trace_path


def trace_path(run_log_path) -> Path | None:
    path = session_trace_path(run_log_path)
    if path is None or not path.parent.is_dir():
        return None
    return path


def append_event(run_log_path, event: dict) -> None:
    path = trace_path(run_log_path)
    if path is None:
        return
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    centis = datetime.now(timezone.utc).microsecond // 10000
    line = f"{stamp}.{centis:02d} · optimizer · {event.get('event', 'event')}"
    if event.get("report"):
        detail = str(event["report"]).replace("\n", " ")
        if len(detail) > 80:
            detail = detail[:77] + "..."
        line += f": {detail}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.write(
            json.dumps(
                {"ts": datetime.now(timezone.utc).isoformat(), **event},
                ensure_ascii=False,
            )
            + "\n"
        )
