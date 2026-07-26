"""Fork traces land in traces/session.md when TraceContext is active."""
import importlib.util
from pathlib import Path

import pytest

from langbridge_code.util.artifacts import create_artifact_session, session_trace_path
from langbridge_code.util.trace_log import begin_trace, end_trace


@pytest.fixture(autouse=True)
def _restore_real_fork_one_pass(monkeypatch):
    """conftest stubs fork_one_pass offline; this module needs the real helper."""
    import langbridge_code.agents.common.fork as fork_mod

    path = Path(fork_mod.__file__)
    spec = importlib.util.spec_from_file_location("_fork_real", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setattr(fork_mod, "fork_one_pass", mod.fork_one_pass)
    yield


def test_fork_one_pass_writes_trace(tmp_path, monkeypatch):
    from langbridge_code.agents.common import fork as fork_mod

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)

    seen = {}

    def fake_create(api_key, model, messages, **kwargs):
        seen["tool_schemas"] = kwargs.get("tool_schemas")
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "#### Next\n- ship it"}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        fake_create,
    )

    schemas = [{"type": "function", "name": "note_progress"}]
    run_log = create_artifact_session("Trace fork")
    begin_trace(run_log, "2026-07-24T010000.00")
    note = fork_mod.fork_one_pass(
        "key",
        "model",
        [{"role": "user", "content": "prior work"}],
        "Write markdown only.",
        label="progress note fork",
        tool_schemas=schemas,
    )
    end_trace()

    assert "ship it" in note
    assert seen["tool_schemas"] is schemas
    text = session_trace_path(run_log).read_text(encoding="utf-8")
    assert "progress note fork" in text
    assert "Write markdown only." in text
    assert "ship it" in text


def test_fork_one_pass_rejects_tool_calls_and_retries(tmp_path, monkeypatch):
    from langbridge_code.agents.common import fork as fork_mod

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    calls = {"n": 0}

    def fake_create(api_key, model, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "note_progress",
                        "call_id": "c1",
                        "arguments": '{"description":"x"}',
                    }
                ]
            }
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "#### Next\n- retry ok"}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        fake_create,
    )

    run_log = create_artifact_session("Reject tools")
    begin_trace(run_log, "2026-07-24T010200.00")
    note = fork_mod.fork_one_pass(
        "key",
        "model",
        [{"role": "user", "content": "prior"}],
        "Write markdown only.",
        label="progress note fork",
    )
    end_trace()

    assert note == "#### Next\n- retry ok"
    assert calls["n"] == 2
    text = session_trace_path(run_log).read_text(encoding="utf-8")
    assert "reject: function_call (note_progress)" in text
    assert "retry ok" in text


def test_fork_one_pass_rejects_dsml_text_and_gives_up_empty(tmp_path, monkeypatch):
    from langbridge_code.agents.common import fork as fork_mod

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)

    def fake_create(api_key, model, messages, **kwargs):
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '<|DSML|tool_calls>\ninvoke name="note_progress"',
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        fake_create,
    )

    run_log = create_artifact_session("DSML spam")
    begin_trace(run_log, "2026-07-24T010300.00")
    note = fork_mod.fork_one_pass(
        "key",
        "model",
        [{"role": "user", "content": "prior"}],
        "Write markdown only.",
        label="progress note fork",
    )
    end_trace()

    assert note == ""
    text = session_trace_path(run_log).read_text(encoding="utf-8")
    assert "reject: tool markup in text" in text


def test_fork_agent_writes_tool_trace(tmp_path, monkeypatch):
    from langbridge_code.agents.common import fork as fork_mod

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    calls = {"n": 0}

    def fake_create(api_key, model, messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "read_file",
                        "call_id": "c1",
                        "arguments": '{"path":"user/memory.md"}',
                    }
                ]
            }
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Memory updated."}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        fake_create,
    )

    run_log = create_artifact_session("Memory fork")
    begin_trace(run_log, "2026-07-24T010100.00")
    report = fork_mod.fork_agent(
        "key",
        "model",
        [{"role": "user", "content": "remember this"}],
        "Maintain memory then exit.",
        tool_schemas=[{"type": "function", "name": "read_file"}],
        tools={"read_file": lambda **kwargs: "index body"},
        label="Memory Writer",
    )
    end_trace()

    assert report == "Memory updated."
    text = session_trace_path(run_log).read_text(encoding="utf-8")
    assert "Memory Writer" in text
    assert "Maintain memory then exit." in text
    assert "→ read_file" in text
    assert "← read_file: index body" in text
    assert "Memory updated." in text
