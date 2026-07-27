"""Unit tests for interactive SWE-Chat commit resolution."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib.resolve_commits import (  # noqa: E402
    CommitRef,
    collect_candidate_commits,
    files_outside_touched,
    pick_by_date,
    resolve_gold_and_base,
)


def _dt(h: int, m: int = 0) -> datetime:
    return datetime(2026, 1, 1, h, m, tzinfo=timezone.utc)


def test_collect_candidates_union_and_sort():
    refs = collect_candidate_commits(
        ["repo#cp1", "repo#cp2"],
        checkpoint_commit_shas={
            "repo#cp1": ["AAA"],
            "repo#cp2": ["BBB", "CCC"],
        },
        commit_meta={
            "AAA": {"commit_date": _dt(10), "commit_index": 0},
            "BBB": {"commit_date": _dt(11), "commit_index": 0},
            "CCC": {"commit_date": _dt(11, 30), "commit_index": 1},
        },
    )
    assert [r.sha for r in refs] == ["AAA", "BBB", "CCC"]
    assert pick_by_date(refs, which="first").sha == "AAA"
    assert pick_by_date(refs, which="last").sha == "CCC"


def test_amend_copies_pick_reachable_gold_and_parent_base():
    # BBB amended away; only CCC reachable from main tip.
    refs = [
        CommitRef("AAA", _dt(10), "repo#cp1", 0),
        CommitRef("BBB", _dt(11), "repo#cp2", 0),
        CommitRef("CCC", _dt(11, 30), "repo#cp2", 1),
    ]
    tip = "MAIN"
    ancestors = {
        # MAIN history: ROOT -> BASE -> AAA -> CCC -> MAIN
        "MAIN": {"MAIN", "CCC", "AAA", "BASE", "ROOT"},
        "CCC": {"CCC", "AAA", "BASE", "ROOT"},
        "AAA": {"AAA", "BASE", "ROOT"},
        "BASE": {"BASE", "ROOT"},
    }
    parents = {"AAA": "BASE", "CCC": "AAA", "BBB": "AAA"}

    def is_ancestor(sha: str, tip_sha: str) -> bool:
        return sha in ancestors.get(tip_sha, set())

    result = resolve_gold_and_base(
        refs,
        default_branch_tip=tip,
        is_ancestor=is_ancestor,
        parent_of=lambda sha: parents.get(sha),
    )
    assert result.ok
    assert result.gold_sha == "CCC"
    assert result.base_sha == "BASE"
    assert result.first_sha == "AAA"
    assert result.last_sha == "CCC"


def test_drop_when_nothing_on_default_branch():
    refs = [CommitRef("AAA", _dt(10)), CommitRef("BBB", _dt(11))]
    result = resolve_gold_and_base(
        refs,
        default_branch_tip="MAIN",
        is_ancestor=lambda sha, tip: False,
        parent_of=lambda sha: "BASE",
    )
    assert not result.ok
    assert "reachable" in result.reason


def test_files_outside_touched():
    dirty = files_outside_touched(
        ["src/a.py", "vendor/other.py"],
        ["src/a.py", "tests/t.py"],
    )
    assert dirty == ["vendor/other.py"]


def test_resolve_caps_long_gold_line_span():
    # 5 session commits on main; cap span to last 2 → base is parent of D (C).
    refs = [
        CommitRef("A", _dt(10)),
        CommitRef("B", _dt(11)),
        CommitRef("C", _dt(12)),
        CommitRef("D", _dt(13)),
        CommitRef("E", _dt(14)),
    ]
    tip = "MAIN"
    ancestors = {
        "MAIN": {"MAIN", "E", "D", "C", "B", "A", "ROOT"},
        "E": {"E", "D", "C", "B", "A", "ROOT"},
        "D": {"D", "C", "B", "A", "ROOT"},
        "C": {"C", "B", "A", "ROOT"},
        "B": {"B", "A", "ROOT"},
        "A": {"A", "ROOT"},
    }
    parents = {"A": "ROOT", "B": "A", "C": "B", "D": "C", "E": "D"}

    result = resolve_gold_and_base(
        refs,
        default_branch_tip=tip,
        is_ancestor=lambda sha, tip_sha: sha in ancestors.get(tip_sha, set()),
        parent_of=lambda sha: parents.get(sha),
        max_span_commits=2,
    )
    assert result.ok
    assert result.gold_sha == "E"
    assert result.base_sha == "C"


def test_pick_instruction_skips_image_only():
    from _lib.quality import pick_instruction

    instruction, followups = pick_instruction(
        ["Let's make this 1 GB\n[Image: image/png]", "Increase file size limit to 1 GB"]
    )
    assert "Increase file size" in instruction
    assert followups == []
