"""CLI entrypoints for enrich / curate / resolve / collect (mocked I/O)."""
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


def test_curate_main_runs_intent_llm(tmp_path, monkeypatch):
    from _lib import paths
    from curate import curate as curate_mod

    inp = tmp_path / "reference.jsonl"
    out = tmp_path / "curate.jsonl"
    drop = tmp_path / "drop.json"
    specs = tmp_path / "specs"
    human = tmp_path / "human_drop.json"
    human.write_text(json.dumps({"dropped": []}), encoding="utf-8")
    _write_jsonl(
        inp,
        [
            {
                "task_id": "t1",
                "session_id": "s",
                "repo": "o/r",
                "base_commit": "b" * 40,
                "gold_commit": "g" * 40,
                "instruction": "Do one foobar thing",
                "followup_prompts": ["Do two foobar"],
                "test_patch": "diff --git a/tests/test_foobar.py b/tests/test_foobar.py\n+assert foobar",
                "gold_code_patch": "cp",
                "fail_to_pass": ["tests/test_foobar.py::test_x"],
                "test_files": ["tests/test_foobar.py"],
                "code_files": ["src/foobar.py"],
                "agent_runtime_sec": 90,
                "horizon": "short",
            }
        ],
    )
    monkeypatch.setattr(paths, "SPECS_DIR", specs)
    monkeypatch.setattr(paths, "DEFAULT_HUMAN_DROP", human)
    monkeypatch.setattr(paths, "spec_path", lambda tid: specs / f"{tid}.json")
    monkeypatch.setattr(
        sys,
        "argv",
        ["curate.py", "--in", str(inp), "--out", str(out), "--drop", str(drop)],
    )
    llm = {
        "intents": [
            {
                "id": "i1",
                "text": "Do one foobar thing",
                "source_turn": 0,
                "revealed_at_start": True,
            },
            {
                "id": "i2",
                "text": "Do two foobar",
                "source_turn": 1,
                "revealed_at_start": False,
            },
        ],
        "session_analysis": "Reveal i2 after i1.",
        "task_type": "feature",
    }
    with patch("intent.analyze.chat_json_ex", return_value=(llm, None)):
        assert curate_mod.main() == 0
    rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(rows) == 1
    assert len(rows[0]["intents"]) == 2
    assert rows[0]["intent_source"] == "llm"
    assert (specs / "t1.json").exists()


def test_curate_main_drops_when_llm_unavailable(tmp_path, monkeypatch):
    from _lib import paths
    from curate import curate as curate_mod

    inp = tmp_path / "reference.jsonl"
    out = tmp_path / "curate.jsonl"
    drop = tmp_path / "drop.json"
    specs = tmp_path / "specs"
    human = tmp_path / "human_drop.json"
    human.write_text(json.dumps({"dropped": []}), encoding="utf-8")
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
                "fail_to_pass": ["tests/t.py::test_a"],
            }
        ],
    )
    monkeypatch.setattr(paths, "SPECS_DIR", specs)
    monkeypatch.setattr(paths, "DEFAULT_HUMAN_DROP", human)
    monkeypatch.setattr(paths, "spec_path", lambda tid: specs / f"{tid}.json")
    monkeypatch.setattr(
        sys,
        "argv",
        ["curate.py", "--in", str(inp), "--out", str(out), "--drop", str(drop)],
    )
    with patch("intent.analyze.chat_json_ex", return_value=(None, "no API key configured")):
        assert curate_mod.main() == 0
    assert not out.exists() or not out.read_text().strip()
    dropped = json.loads(drop.read_text())["dropped"]
    assert dropped[0]["task_id"] == "t1"
    assert "no API key" in dropped[0]["reason"]


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
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.setattr(
        llm,
        "resolve_chat_route",
        lambda **_kwargs: (None, "no API key (LangBridge config api_keys.*)"),
    )
    assert llm.chat_json(system="s", user="u") is None
    parsed, error = llm.chat_json_ex(system="s", user="u")
    assert parsed is None
    assert "no API key" in error


def test_resolve_chat_route_uses_langbridge_config(monkeypatch):
    from _lib import llm
    import sys
    import types

    settings_mod = types.ModuleType("langbridge_code.settings")
    settings_mod.DEFAULT_MODEL = "deepseek-v4-pro"
    settings_mod.resolve_llm_route = lambda model: {
        "provider": "deepseek",
        "api_key": "sk-from-config",
        "base_url": "https://api.deepseek.com",
    }
    settings_mod.active_api_provider = lambda: "deepseek"
    settings_mod.resolve_provider_api_key = lambda _name: None
    settings_mod.provider_base_url = lambda _name: "https://api.deepseek.com"
    settings_mod.load_config = lambda: {
        "api": {"providers": {"deepseek": {"model": "deepseek-v4-pro"}}}
    }
    package = types.ModuleType("langbridge_code")
    package.settings = settings_mod
    monkeypatch.setitem(sys.modules, "langbridge_code", package)
    monkeypatch.setitem(sys.modules, "langbridge_code.settings", settings_mod)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LB_INTERACTIVE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    route, err = llm.resolve_chat_route()
    assert err is None
    assert route["api_key"] == "sk-from-config"
    assert route["base_url"] == "https://api.deepseek.com"
    assert "deepseek" in route["model"]


def test_chat_json_ex_retries_transient_then_reports(monkeypatch):
    from _lib import llm

    monkeypatch.setattr(
        llm,
        "resolve_chat_route",
        lambda **_kwargs: (
            {
                "api_key": "k",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "provider": "openai",
            },
            None,
        ),
    )
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def boom(base, key, payload):
        calls["n"] += 1
        raise llm.LLMError("network: timed out")

    monkeypatch.setattr(llm, "_post_chat", boom)
    parsed, error = llm.chat_json_ex(system="s", user="u", retries=3)
    assert parsed is None
    assert calls["n"] == 3  # transient → retried to the cap
    assert "timed out" in error


def test_chat_json_ex_gives_up_immediately_on_fatal(monkeypatch):
    from _lib import llm

    monkeypatch.setattr(
        llm,
        "resolve_chat_route",
        lambda **_kwargs: (
            {
                "api_key": "k",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "provider": "openai",
            },
            None,
        ),
    )
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def boom(base, key, payload):
        calls["n"] += 1
        raise llm.LLMError("HTTP 401 bad key", fatal=True)

    monkeypatch.setattr(llm, "_post_chat", boom)
    parsed, error = llm.chat_json_ex(system="s", user="u", retries=3)
    assert parsed is None
    # A bad key can't be fixed by stripping params or retrying — stop at once.
    assert calls["n"] == 1
    assert "401" in error


def test_chat_json_ex_strips_rejected_optional_params(monkeypatch):
    """Providers that reject json_object/temperature get a free param-less retry."""
    from _lib import llm

    monkeypatch.setattr(
        llm,
        "resolve_chat_route",
        lambda **_kwargs: (
            {
                "api_key": "k",
                "base_url": "https://api.anthropic.com/v1",
                "model": "claude-fable-5",
                "provider": "anthropic",
            },
            None,
        ),
    )
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    seen_payloads = []

    def picky(base, key, payload):
        seen_payloads.append(dict(payload))
        if "response_format" in payload:
            raise llm.LLMError(
                "HTTP 400 response_format.type: Input should be 'json_schema'",
                fatal=True,
            )
        if "temperature" in payload:
            raise llm.LLMError(
                "HTTP 400 Unsupported value: 'temperature' does not support 0",
                fatal=True,
            )
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    monkeypatch.setattr(llm, "_post_chat", picky)
    parsed, error = llm.chat_json_ex(system="s", user="u", retries=1)
    assert error is None
    assert parsed == {"ok": True}
    # Strip retries are free: they must not consume the single allowed attempt.
    assert len(seen_payloads) == 3
    assert "response_format" not in seen_payloads[-1]
    assert "temperature" not in seen_payloads[-1]
