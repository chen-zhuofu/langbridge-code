"""Derive agent runtime from conversation timestamps."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

# E2E eval floor: at least 40 minutes, or 2× original agent runtime (whichever larger).
EVAL_TIMEOUT_FLOOR_SEC = 2400.0


def _as_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _role(row: dict[str, Any]) -> str:
    role = str(row.get("role") or row.get("speaker") or "").lower().strip()
    if role:
        return role
    if row.get("is_user") is True:
        return "user"
    if row.get("is_assistant") is True:
        return "assistant"
    return ""


def _is_user(row: dict[str, Any]) -> bool:
    return _role(row) in {"user", "human", "prompt"}


def _is_assistant(row: dict[str, Any]) -> bool:
    return _role(row) in {"assistant", "agent", "model", "bot", "ai"}


def _turn_timestamp(row: dict[str, Any]) -> datetime | None:
    return _as_dt(row.get("timestamp") or row.get("created_at"))


def agent_runtime_seconds(
    turns: Iterable[dict[str, Any]],
    *,
    idle_gap_sec: float | None = None,  # kept for call-site compat; unused
) -> float | None:
    """Sum user→assistant reply gaps (= assistant running time).

    For each user message, take the next assistant message's timestamp minus the
    user timestamp. Intermediate idle between assistant and the next user is
    not counted. Long agent runs (even >2 minutes) are counted in full.

    Returns None if no complete user→assistant pair exists.
    """
    del idle_gap_sec  # deprecated; previously dropped gaps >2min
    rows = [r for r in turns if isinstance(r, dict)]
    rows.sort(
        key=lambda r: str(
            r.get("timestamp")
            or r.get("created_at")
            or r.get("conversation_turn_number")
            or ""
        )
    )
    total = 0.0
    pairs = 0
    last_user_ts: datetime | None = None
    for row in rows:
        ts = _turn_timestamp(row)
        if ts is None:
            continue
        if _is_user(row):
            last_user_ts = ts
            continue
        if _is_assistant(row) and last_user_ts is not None:
            gap = (ts - last_user_ts).total_seconds()
            if gap >= 0:
                total += gap
                pairs += 1
            last_user_ts = None  # one assistant reply per user prompt
    return total if pairs else None


def eval_timeout_sec(agent_runtime_sec: float | None) -> float:
    """Interactive-bench e2e ceiling: ``max(40 minutes, 2 × agent runtime)``."""
    if agent_runtime_sec is None:
        return float(EVAL_TIMEOUT_FLOOR_SEC)
    return max(float(EVAL_TIMEOUT_FLOOR_SEC), float(agent_runtime_sec) * 2.0)
