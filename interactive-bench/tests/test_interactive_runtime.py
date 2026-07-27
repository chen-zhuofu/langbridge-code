"""Agent runtime derivation from timestamps."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib.runtime import agent_runtime_seconds  # noqa: E402


def test_agent_runtime_drops_long_idle():
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    turns = [
        {"timestamp": t0},
        {"timestamp": t0 + timedelta(seconds=30)},
        # 10 minute idle — excluded
        {"timestamp": t0 + timedelta(seconds=30 + 600)},
        {"timestamp": t0 + timedelta(seconds=30 + 600 + 20)},
    ]
    runtime = agent_runtime_seconds(turns, idle_gap_sec=120)
    assert runtime == 50.0


def test_agent_runtime_none_with_one_stamp():
    assert agent_runtime_seconds([{"timestamp": "2026-01-01T00:00:00Z"}]) is None
