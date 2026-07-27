"""Collect stage filters and row shaping."""
from __future__ import annotations

import sys
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from collect.collect import session_to_row  # noqa: E402


class _Row(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_drop_mind_changer():
    row = _Row(
        session_id="abc",
        repo_id="org/repo",
        user_persona="Mind Changer",
        checkpoint_ids=["cp1"],
        prompt_count=2,
    )
    out = session_to_row(row)
    assert out["_drop"] is True
    assert "Mind Changer" in out["reason"]


def test_drop_no_checkpoints():
    row = _Row(
        session_id="abc",
        repo_id="org/repo",
        user_persona="Patient",
        checkpoint_ids=[],
        prompt_count=1,
    )
    out = session_to_row(row)
    assert out["_drop"] is True
    assert "checkpoint" in out["reason"]


def test_keep_session_shapes_task_id():
    row = _Row(
        session_id="deadbeef-1234",
        repo_id="org/repo",
        user_persona="Patient Collaborator",
        checkpoint_ids='["cp1"]',
        prompt_count=3,
        files_touched='["src/a.py"]',
        prompt_intents='["debug"]',
    )
    out = session_to_row(row)
    assert out is not None
    assert not out.get("_drop")
    assert out["task_id"] == "org__repo__deadbeef"
    assert out["checkpoint_ids"] == ["cp1"]
    assert out["files_touched"] == ["src/a.py"]
    assert out["prompt_intents"] == ["debug"]
