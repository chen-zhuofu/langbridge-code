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

from _lib.runtime import agent_runtime_seconds, eval_timeout_sec  # noqa: E402


def test_agent_runtime_sums_user_to_assistant_gaps():
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    turns = [
        {"timestamp": t0, "role": "user", "content": "go"},
        # 3 minute agent run — must count (old idle filter would drop this)
        {"timestamp": t0 + timedelta(seconds=180), "role": "assistant", "content": "done"},
        # 10 minute user idle — must NOT count
        {"timestamp": t0 + timedelta(seconds=180 + 600), "role": "user", "content": "more"},
        {"timestamp": t0 + timedelta(seconds=180 + 600 + 20), "role": "assistant", "content": "ok"},
    ]
    runtime = agent_runtime_seconds(turns)
    assert runtime == 200.0  # 180 + 20


def test_agent_runtime_none_without_assistant_reply():
    assert agent_runtime_seconds(
        [{"timestamp": "2026-01-01T00:00:00Z", "role": "user", "content": "hi"}]
    ) is None
    assert agent_runtime_seconds([{"timestamp": "2026-01-01T00:00:00Z"}]) is None


def test_eval_timeout_is_max_of_40m_and_2x_runtime():
    assert eval_timeout_sec(None) == 2400.0
    assert eval_timeout_sec(100) == 2400.0  # 2×100 < 40m
    assert eval_timeout_sec(2000) == 4000.0  # 2×2000 > 40m
