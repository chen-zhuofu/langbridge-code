"""Derive agent runtime from conversation timestamps (SWE-Chat paper style)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

# Idle gap longer than this is excluded from agent runtime (paper: >2 min).
DEFAULT_IDLE_GAP_SEC = 120.0


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


def agent_runtime_seconds(
    turns: Iterable[dict[str, Any]],
    *,
    idle_gap_sec: float = DEFAULT_IDLE_GAP_SEC,
) -> float | None:
    """Sum active spans between consecutive timestamps, dropping long idles.

    Expects rows with a ``timestamp`` field (conversation / transcript turns).
    Returns None if fewer than two usable timestamps.
    """
    times: list[datetime] = []
    for row in turns:
        dt = _as_dt(row.get("timestamp"))
        if dt is not None:
            times.append(dt)
    if len(times) < 2:
        return None
    times.sort()
    total = 0.0
    for prev, cur in zip(times, times[1:]):
        gap = (cur - prev).total_seconds()
        if gap < 0:
            continue
        if gap <= idle_gap_sec:
            total += gap
    return total
