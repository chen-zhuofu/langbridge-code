"""Resolve stage with mocked GitHub helpers."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from resolve.resolve import resolve_one  # noqa: E402


def _dt(h: int) -> datetime:
    return datetime(2026, 1, 1, h, tzinfo=timezone.utc)


def test_resolve_one_ok():
    inst = {
        "task_id": "org__repo__sess",
        "session_id": "sess",
        "repo": "org/repo",
        "checkpoint_ids": ["cp1", "cp2"],
    }
    cp_to_shas = {"cp1": ["AAA"], "cp2": ["BBB"]}
    commit_meta = {
        "AAA": {"commit_date": _dt(10), "commit_index": 0, "checkpoint_pk": "cp1"},
        "BBB": {"commit_date": _dt(11), "commit_index": 0, "checkpoint_pk": "cp2"},
    }
    # History: ROOT -> BASE -> AAA -> BBB -> MAIN
    ancestors = {
        "MAIN": {"MAIN", "BBB", "AAA", "BASE", "ROOT"},
        "BBB": {"BBB", "AAA", "BASE", "ROOT"},
        "AAA": {"AAA", "BASE", "ROOT"},
    }
    parents = {"AAA": "BASE", "BBB": "AAA"}

    with patch("resolve.resolve.default_branch", return_value="main"), patch(
        "resolve.resolve.branch_tip", return_value="MAIN"
    ), patch(
        "resolve.resolve.is_ancestor_api",
        side_effect=lambda repo, sha, tip: sha in ancestors.get(tip, set()),
    ), patch(
        "resolve.resolve.first_parent",
        side_effect=lambda repo, sha: parents.get(sha),
    ):
        out = resolve_one(inst, cp_to_shas, commit_meta)

    assert not out.get("_drop")
    assert out["gold_commit"] == "BBB"
    assert out["base_commit"] == "BASE"
    assert out["default_branch"] == "main"


def test_resolve_one_drops_unreachable():
    inst = {
        "task_id": "org__repo__sess",
        "session_id": "sess",
        "repo": "org/repo",
        "checkpoint_ids": ["cp1"],
    }
    cp_to_shas = {"cp1": ["AAA"]}
    commit_meta = {"AAA": {"commit_date": _dt(10), "commit_index": 0}}

    with patch("resolve.resolve.default_branch", return_value="main"), patch(
        "resolve.resolve.branch_tip", return_value="MAIN"
    ), patch("resolve.resolve.is_ancestor_api", return_value=False), patch(
        "resolve.resolve.first_parent", return_value="BASE"
    ):
        out = resolve_one(inst, cp_to_shas, commit_meta)

    assert out["_drop"] is True
    assert "reachable" in out["reason"]
