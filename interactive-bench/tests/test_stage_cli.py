"""CLI entrypoints for enrich / intent / resolve / collect (mocked I/O)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


DIFF = """diff --git a/src/alphamod.py b/src/alphamod.py
index 1..2 100644
--- a/src/alphamod.py
+++ b/src/alphamod.py
@@ -1 +1 @@
-1
+2
diff --git a/tests/test_alphamod.py b/tests/test_alphamod.py
index 3..4 100644
--- a/tests/test_alphamod.py
+++ b/tests/test_alphamod.py
@@ -1 +1 @@
-x
+y
"""


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_enrich_main_keeps_and_drops(tmp_path, monkeypatch):
    from enrich import enrich as enrich_mod

    inp = tmp_path / "resolve.jsonl"
    out = tmp_path / "enrich.jsonl"
    drop = tmp_path / "drop.json"
    good = {
        "task_id": "good",
        "session_id": "s1",
        "repo": "o/r",
        "base_commit": "b" * 40,
        "gold_commit": "g" * 40,
        "files_touched": ["src/alphamod.py", "tests/test_alphamod.py"],
        "instruction": "Fix alphamod return value for the unit suite",
    }
    dirty = {
        **good,
        "task_id": "dirty",
        "session_id": "s2",
        "files_touched": ["README.md"],  # zero overlap with sample diff → drop
    }
    _write_jsonl(inp, [good, dirty])

    def fake_diff(repo, base, gold):
        return DIFF

    monkeypatch.setattr(
        sys,
        "argv",
        ["enrich.py", "--in", str(inp), "--out", str(out), "--drop", str(drop), "--limit", "10"],
    )
    with patch("enrich.enrich.compare_diff", side_effect=fake_diff), patch(
        "enrich.enrich.compare_commits", return_value=[]
    ), patch("enrich.enrich.compare_ahead_by", return_value=1):
        assert enrich_mod.main() == 0

    kept = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert {r["task_id"] for r in kept} == {"good"}
    dropped = json.loads(drop.read_text())["dropped"]
    assert any(d["task_id"] == "dirty" for d in dropped)


def test_intent_main_heuristic(tmp_path, monkeypatch):
    from intent import analyze as analyze_mod

    inp = tmp_path / "enrich.jsonl"
    out = tmp_path / "intent.jsonl"
    drop = tmp_path / "drop.json"
    _write_jsonl(
        inp,
        [
            {
                "task_id": "t1",
                "session_id": "s",
                "repo": "o/r",
                "base_commit": "b" * 40,
                "gold_commit": "g" * 40,
                "instruction": "Do one",
                "followup_prompts": ["Do two"],
                "test_patch": "x",
            }
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["analyze.py", "--in", str(inp), "--out", str(out), "--drop", str(drop)],
    )
    with patch("intent.analyze.chat_json", return_value=None):
        assert analyze_mod.main() == 0
    rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(rows) == 1
    assert len(rows[0]["intents"]) == 2


def test_resolve_main_with_mocks(tmp_path, monkeypatch):
    from resolve import resolve as resolve_mod

    inp = tmp_path / "sessions.jsonl"
    out = tmp_path / "resolved.jsonl"
    drop = tmp_path / "drop.json"
    data_dir = tmp_path / "swe"
    data_dir.mkdir()
    _write_jsonl(
        inp,
        [
            {
                "task_id": "org__repo__abcd",
                "session_id": "abcd",
                "repo": "org/repo",
                "checkpoint_ids": ["cp1", "cp2"],
            }
        ],
    )

    cp_to_shas = {"cp1": ["AAA"], "cp2": ["BBB"]}
    commit_meta = {
        "AAA": {
            "commit_date": datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
            "commit_index": 0,
            "checkpoint_pk": "cp1",
        },
        "BBB": {
            "commit_date": datetime(2026, 1, 1, 11, tzinfo=timezone.utc),
            "commit_index": 0,
            "checkpoint_pk": "cp2",
        },
    }
    ancestors = {
        "MAIN": {"MAIN", "BBB", "AAA", "BASE"},
        "BBB": {"BBB", "AAA", "BASE"},
        "AAA": {"AAA", "BASE"},
    }
    parents = {"AAA": "BASE", "BBB": "AAA"}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resolve.py",
            "--data-dir",
            str(data_dir),
            "--in",
            str(inp),
            "--out",
            str(out),
            "--drop",
            str(drop),
            "--limit",
            "1",
        ],
    )
    with patch(
        "resolve.resolve.load_checkpoint_maps", return_value=(cp_to_shas, commit_meta)
    ), patch("resolve.resolve.default_branch", return_value="main"), patch(
        "resolve.resolve.branch_tip", return_value="MAIN"
    ), patch(
        "resolve.resolve.is_ancestor_api",
        side_effect=lambda repo, sha, tip: sha in ancestors.get(tip, set()),
    ), patch(
        "resolve.resolve.first_parent",
        side_effect=lambda repo, sha: parents.get(sha),
    ):
        assert resolve_mod.main() == 0

    rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert rows[0]["gold_commit"] == "BBB"
    assert rows[0]["base_commit"] == "BASE"


def test_collect_main_from_fake_frame(tmp_path, monkeypatch):
    from collect import collect as collect_mod

    class _Row:
        def __init__(self, data):
            self._data = data

        def get(self, key, default=None):
            return self._data.get(key, default)

    class _Frame:
        def __init__(self, rows):
            self._rows = rows

        def __len__(self):
            return len(self._rows)

        def iterrows(self):
            for index, row in enumerate(self._rows):
                yield index, row

    frame = _Frame(
        [
            _Row(
                {
                    "session_id": "aaaaaaaa-1111",
                    "repo_id": "org/repo",
                    "user_persona": "Patient",
                    "checkpoint_ids": '["cp1"]',
                    "prompt_count": 2,
                    "files_touched": '["a.py"]',
                    "canonical_checkpoint_pk": "cp1",
                    "duration_seconds": 10,
                    "branch": "main",
                    "agent": "claude",
                    "turn_count": 4,
                    "created_at": "2026-01-01",
                }
            ),
            _Row(
                {
                    "session_id": "bbbbbbbb-2222",
                    "repo_id": "org/repo",
                    "user_persona": "Mind Changer",
                    "checkpoint_ids": '["cp2"]',
                    "prompt_count": 2,
                    "files_touched": "[]",
                    "canonical_checkpoint_pk": "cp2",
                    "duration_seconds": 10,
                    "branch": "main",
                    "agent": "claude",
                    "turn_count": 4,
                    "created_at": "2026-01-01",
                }
            ),
        ]
    )
    data_dir = tmp_path / "swe"
    data_dir.mkdir()
    out = tmp_path / "sessions.jsonl"
    drop = tmp_path / "drop.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect.py",
            "--data-dir",
            str(data_dir),
            "--out",
            str(out),
            "--drop",
            str(drop),
            "--limit",
            "10",
        ],
    )
    with patch("collect.collect.load_sessions_from_parquet", return_value=frame):
        assert collect_mod.main() == 0
    kept = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(kept) == 1
    assert kept[0]["task_id"].startswith("org__repo__")
    dropped = json.loads(drop.read_text())["dropped"]
    assert any("Mind Changer" in d["reason"] for d in dropped)

def test_io_append_drop(tmp_path):
    from _lib.io_util import append_drop, load_json

    path = tmp_path / "drop.json"
    append_drop(path, "a", "r1")
    append_drop(path, "a", "r2")  # replace
    append_drop(path, "b", "r3")
    data = load_json(path)
    assert len(data["dropped"]) == 2
    assert {d["task_id"]: d["reason"] for d in data["dropped"]} == {"a": "r2", "b": "r3"}


def test_llm_returns_none_without_key(monkeypatch):
    from _lib import llm

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LB_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.chat_json(system="s", user="u") is None
