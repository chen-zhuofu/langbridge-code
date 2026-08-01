"""Tests for curate problem-statement tracker-ref stripping."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PIPELINE = Path(__file__).resolve().parents[2] / "eval" / "data-pipeline"
_CURATE = _PIPELINE / "curate"
for _path in (_PIPELINE, _CURATE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from curate import (  # noqa: E402
    CURATE_SYSTEM,
    _clip,
    _user_payload,
    strip_tracker_refs,
)


def test_strips_possibly_related_issue_line():
    text = (
        "All my tests are now failing.\n\n"
        "### Questions\n\n"
        "* Is this a bug?\n\n"
        "Possibly related: #14635\n"
    )
    out = strip_tracker_refs(text)
    assert "#14635" not in out
    assert "Possibly related" not in out
    assert "Is this a bug?" in out


def test_strips_github_issue_and_pull_urls():
    text = (
        "See https://github.com/tqdm/tqdm/issues/624 for background.\n"
        "Fixed in https://github.com/psf/requests/pull/6644.\n"
        "Keep https://docs.python.org/3/library/configparser.html#unnamed-sections.\n"
    )
    out = strip_tracker_refs(text)
    assert "github.com/tqdm/tqdm/issues/624" not in out
    assert "github.com/psf/requests/pull/6644" not in out
    assert "docs.python.org" in out


def test_keeps_blob_line_anchors():
    text = (
        "Code at https://github.com/networkx/networkx/blob/abc/mst.py#L1053).\n"
        "Also related #8556 elsewhere.\n"
    )
    out = strip_tracker_refs(text)
    assert "#L1053" in out
    assert "#8556" not in out


def test_strips_jira_url_and_key():
    text = "Tracked in https://jira.example.com/browse/PROJ-1234 and PROJ-1234.\n"
    out = strip_tracker_refs(text)
    assert "browse/PROJ-1234" not in out
    assert "PROJ-1234" not in out


def test_keeps_iso8601_and_utf8_lookalikes():
    text = "Use ISO-8601 dates and UTF-8 encoding.\n"
    out = strip_tracker_refs(text)
    assert "ISO-8601" in out
    assert "UTF-8" in out


@pytest.mark.parametrize(
    "text",
    [
        "fixes #12",
        "Closes: #9999",
        "see also #42",
    ],
)
def test_strips_prose_issue_phrases(text):
    assert "#" not in strip_tracker_refs(f"Bug. {text}. Done.\n")


def test_curate_policy_keep_salvage_rewrite_or_drop():
    assert "KEEP when the problem statement is already a usable coding task" in CURATE_SYSTEM
    assert "REWRITE only in this salvage case" in CURATE_SYSTEM
    assert "Do NOT leak the solution" in CURATE_SYSTEM
    assert "DROP in every other bad case" in CURATE_SYSTEM
    assert "requiring another repository" in CURATE_SYSTEM
    assert "do not rewrite around it" in CURATE_SYSTEM


def test_user_payload_includes_clipped_test_patch():
    import json

    payload = json.loads(
        _user_payload(
            {
                "task_id": "repo__1",
                "repo": "org/repo",
                "problem_statement": "Fix caching.",
                "fail_to_pass": ["tests/test_a.py::test_one"],
                "test_patch": "x" * 20_000,
            }
        )
    )
    assert payload["fail_to_pass_names"] == ["tests/test_a.py::test_one"]
    assert len(payload["test_patch"]) < 20_000
    assert "truncated" in payload["test_patch"]
    assert "HIDDEN" in payload["notes"]


def test_clip_short_unchanged():
    assert _clip("hello", 100) == "hello"
