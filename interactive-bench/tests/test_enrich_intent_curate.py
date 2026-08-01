"""Enrich + intent + curate pipeline pieces with mocks (no network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib.spec import build_interactive_spec  # noqa: E402
from enrich.enrich import enrich_one  # noqa: E402
from intent.analyze import analyze_one  # noqa: E402

SAMPLE_DIFF = """diff --git a/src/foobar.py b/src/foobar.py
index 111..222 100644
--- a/src/foobar.py
+++ b/src/foobar.py
@@ -1 +1 @@
-old
+new
diff --git a/tests/test_foobar.py b/tests/test_foobar.py
index 333..444 100644
--- a/tests/test_foobar.py
+++ b/tests/test_foobar.py
@@ -1 +1 @@
-assert False
+assert True
"""


def _resolved(**kwargs):
    base = {
        "task_id": "org__repo__abcd1234",
        "session_id": "abcd1234-ffff",
        "repo": "org/repo",
        "base_commit": "base" * 10,
        "gold_commit": "gold" * 10,
        "files_touched": ["src/foobar.py", "tests/test_foobar.py"],
        "instruction": "Fix foobar checkpoint embedding resume logic",
        "prompt_count": 2,
        "prompt_intents": ["debug"],
    }
    base.update(kwargs)
    return base


def test_enrich_ok_splits_patches():
    with patch("enrich.enrich.compare_diff", return_value=SAMPLE_DIFF), patch(
        "enrich.enrich.compare_files", return_value=[]
    ), patch("enrich.enrich.compare_commits", return_value=[]), patch(
        "enrich.enrich.compare_ahead_by", return_value=1
    ):
        out = enrich_one(_resolved(), data_dir=None)
    assert not out.get("_drop"), out.get("reason")
    assert "tests/test_foobar.py" in out["test_patch"]
    assert "src/foobar.py" in out["gold_code_patch"]
    assert out["horizon"] == "unknown"  # no runtime
    assert out["task_type"] == "bug_fix"
    assert out["docker_image"].startswith("lb-interactive:")


def test_enrich_keeps_partial_files_touched_overlap():
    # files_touched incomplete vs diff extras — still keep if overlap exists
    with patch("enrich.enrich.compare_diff", return_value=SAMPLE_DIFF), patch(
        "enrich.enrich.compare_files", return_value=[]
    ), patch("enrich.enrich.compare_commits", return_value=[]), patch(
        "enrich.enrich.compare_ahead_by", return_value=1
    ):
        out = enrich_one(
            _resolved(files_touched=["src/foobar.py"]),  # missing test file in touched
            data_dir=None,
        )
    assert not out.get("_drop"), out.get("reason")
    assert "tests/test_foobar.py" in (out.get("files_touched_extras") or [])


def test_enrich_drops_zero_overlap_files_touched():
    with patch("enrich.enrich.compare_diff", return_value=SAMPLE_DIFF), patch(
        "enrich.enrich.compare_commits", return_value=[]
    ), patch("enrich.enrich.compare_ahead_by", return_value=1):
        out = enrich_one(
            _resolved(files_touched=["README.md"]),
            data_dir=None,
        )
    assert out["_drop"] is True
    assert "no overlap" in out["reason"]


def test_enrich_drops_empty_diff():
    with patch("enrich.enrich.compare_diff", return_value="   "), patch(
        "enrich.enrich.compare_commits", return_value=[]
    ), patch("enrich.enrich.compare_ahead_by", return_value=1):
        out = enrich_one(_resolved(), data_dir=None)
    assert out["_drop"] is True
    assert "empty" in out["reason"]


def test_enrich_drops_no_test_patch():
    code_only = """diff --git a/src/foobar.py b/src/foobar.py
index 111..222 100644
--- a/src/foobar.py
+++ b/src/foobar.py
@@ -1 +1 @@
-old
+new
"""
    with patch("enrich.enrich.compare_diff", return_value=code_only), patch(
        "enrich.enrich.compare_commits", return_value=[]
    ), patch("enrich.enrich.compare_ahead_by", return_value=1):
        out = enrich_one(_resolved(), data_dir=None)
    assert out["_drop"] is True
    assert "no test" in out["reason"]


def test_enrich_uses_conversation_runtime(tmp_path):
    # No parquet: load_conversation_turns returns []. Patch it.
    turns = [
        {
            "timestamp": "2026-01-01T12:00:00Z",
            "role": "user",
            "content": "Fix foobar checkpoint embedding resume logic",
        },
        {"timestamp": "2026-01-01T12:00:30Z", "role": "assistant", "content": "ok"},
        {
            "timestamp": "2026-01-01T12:01:00Z",
            "role": "user",
            "content": "Also cover the foobar unit tests",
        },
    ]
    with patch("enrich.enrich.compare_diff", return_value=SAMPLE_DIFF), patch(
        "enrich.enrich.load_conversation_turns", return_value=turns
    ), patch("enrich.enrich.compare_commits", return_value=[]), patch(
        "enrich.enrich.compare_ahead_by", return_value=1
    ):
        out = enrich_one(_resolved(instruction=""), data_dir=tmp_path)
    assert out["instruction"] == "Fix foobar checkpoint embedding resume logic"
    assert out["followup_prompts"] == ["Also cover the foobar unit tests"]
    assert out["agent_runtime_sec"] == 30.0
    assert out["horizon"] == "short"


def test_analyze_drops_without_llm():
    with patch("intent.analyze.chat_json_ex", return_value=(None, "no API key")):
        out = analyze_one(
            {
                **_resolved(),
                "instruction": "Do A",
                "followup_prompts": ["Then B"],
                "test_patch": "x",
            },
            data_dir=None,
        )
    assert out.get("_drop") is True
    assert "no API key" in out["reason"]


def test_analyze_uses_llm_payload_when_present():
    llm = {
        "intents": [
            {
                "id": "i1",
                "text": "Rate limit login",
                "source_turn": 0,
                "revealed_at_start": True,
            },
            {
                "id": "i2",
                "text": "Clear error codes",
                "source_turn": 1,
                "revealed_at_start": False,
            },
        ],
        "session_analysis": "Reveal i2 after done.",
        "task_type": "feature",
    }
    with patch("intent.analyze.chat_json_ex", return_value=(llm, None)):
        out = analyze_one(
            {**_resolved(), "instruction": "Rate limit login", "followup_prompts": ["codes"]},
            data_dir=None,
        )
    assert not out.get("_drop"), out.get("reason")
    assert out["task_type"] == "feature"
    assert out["session_analysis"] == "Reveal i2 after done."
    assert out["intent_source"] == "llm"
    assert [i["id"] for i in out["intents"]] == ["i1", "i2"]


def test_build_interactive_spec_sim_timeout():
    spec = build_interactive_spec(
        {
            **_resolved(),
            "instruction": "Fix it",
            "agent_runtime_sec": 100,
            "intents": [{"id": "i1", "text": "Fix it", "revealed_at_start": True}],
            "fail_to_pass": ["t::a"],
            "test_patch": "tp",
            "gold_code_patch": "cp",
            "test_files": ["tests/t.py"],
            "session_analysis": "be quiet",
            "difficulty": "easy",
            "horizon": "short",
            "task_type": "bug_fix",
        }
    )
    assert spec["sim"]["noop_message"] == "continue"
    assert spec["sim"]["max_consecutive_noops"] == 4
    assert spec["sim"]["timeout_sec"] == 2400.0
    assert spec["sim"]["session_analysis"] == "be quiet"
    assert spec["fail_to_pass"] == ["t::a"]
    assert spec["test_files"] == ["tests/t.py"]
    assert spec["difficulty"] == "easy"
    assert spec["horizon"] == "short"


def test_curate_writes_spec(tmp_path, monkeypatch):
    from _lib import paths
    from curate import curate as curate_mod

    specs_dir = tmp_path / "specs"
    out_jsonl = tmp_path / "curate" / "instances.jsonl"
    drop = tmp_path / "curate" / "drop.json"
    inp = tmp_path / "reference" / "instances.jsonl"
    inp.parent.mkdir(parents=True)
    human = tmp_path / "drop" / "drop.json"
    human.parent.mkdir(parents=True)
    human.write_text(json.dumps({"dropped": []}), encoding="utf-8")

    row = {
        **_resolved(),
        "instruction": "Fix foobar behavior in the module",
        "fail_to_pass": ["tests/test_foobar.py::test_x"],
        "pass_to_pass": [],
        "test_patch": "diff --git a/tests/test_foobar.py b/tests/test_foobar.py\n+assert foobar",
        "gold_code_patch": "cp",
        "code_files": ["src/foobar.py"],
        "test_files": ["tests/test_foobar.py"],
        "agent_runtime_sec": 90,
        "horizon": "short",
        "task_type": "bug_fix",
    }
    inp.write_text(json.dumps(row) + "\n", encoding="utf-8")

    monkeypatch.setattr(paths, "SPECS_DIR", specs_dir)
    monkeypatch.setattr(paths, "DEFAULT_HUMAN_DROP", human)
    monkeypatch.setattr(
        paths,
        "spec_path",
        lambda tid: specs_dir / f"{tid}.json",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "curate.py",
            "--in",
            str(inp),
            "--out",
            str(out_jsonl),
            "--drop",
            str(drop),
            "--limit",
            "1",
        ],
    )
    llm = {
        "intents": [
            {
                "id": "i1",
                "text": "Fix foobar behavior",
                "source_turn": 0,
                "revealed_at_start": True,
            }
        ],
        "session_analysis": "quiet",
        "task_type": "bug_fix",
    }
    with patch("intent.analyze.chat_json_ex", return_value=(llm, None)):
        assert curate_mod.main() == 0
    written = specs_dir / f"{row['task_id']}.json"
    assert written.exists()
    spec = json.loads(written.read_text(encoding="utf-8"))
    assert spec["task_id"] == row["task_id"]
    assert spec["difficulty"] == "easy"  # computed from code_files/gold_code_patch/F2P at curate
    assert spec["horizon"] == "short"
    assert spec["sim"]["timeout_sec"] == 2400.0
