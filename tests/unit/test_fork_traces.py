"""Fork traces land in traces/session.md when TraceContext is active."""
import importlib.util
from pathlib import Path

from langbridge_code.util.artifacts import create_artifact_session, session_trace_path
from langbridge_code.util.trace_log import begin_trace, end_trace


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


def test_fork_progress_note_edits_and_denies_other_tools(tmp_path, monkeypatch):
    import json
    import re

    from langbridge_code.agents.common import fork as fork_mod
    from langbridge_code.util.progress import progress_path, read_progress

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    # Bypass the offline stub from conftest.
    path = Path(fork_mod.__file__)
    spec = importlib.util.spec_from_file_location("_fork_real", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setattr(fork_mod, "fork_progress_note", mod.fork_progress_note)

    calls = {"n": 0}
    notes_path = {"value": None}

    def fake_create(api_key, model, messages, **kwargs):
        calls["n"] += 1
        # Recover the absolute notes path from the instruction message.
        if notes_path["value"] is None:
            for message in messages:
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, str):
                    continue
                match = re.search(r"Use Edit with path exactly: (.+)", content)
                if match:
                    notes_path["value"] = match.group(1).strip()
                    break
        target = notes_path["value"]
        assert target, "notes path missing from instruction"
        if calls["n"] == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "deny1",
                        "arguments": '{"command":"ls"}',
                    }
                ]
            }
        if calls["n"] == 2:
            # After deny, Edit the Key discoveries section body.
            before = Path(target).read_text(encoding="utf-8")
            old = "#### Key discoveries\n_Facts learned, with path:line pointers when known._\n"
            assert old in before
            new = old + "\n- parser fixed\n"
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "Edit",
                        "call_id": "edit1",
                        "arguments": json.dumps(
                            {
                                "description": "add discovery",
                                "path": target,
                                "old_string": old,
                                "new_string": new,
                            }
                        ),
                    }
                ]
            }
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        fake_create,
    )

    run_log = create_artifact_session("Progress edit")
    begin_trace(run_log, "2026-07-24T010400.00")
    result = fork_mod.fork_progress_note(
        "key",
        "model",
        [{"role": "user", "content": "fixed the parser"}],
        run_log_path=run_log,
        tool_schemas=[
            {"type": "function", "name": "bash"},
            {"type": "function", "name": "Edit"},
        ],
        label="progress note fork",
    )
    end_trace()

    assert result.startswith("Noted")
    assert "parser fixed" in read_progress(run_log)
    text = session_trace_path(run_log).read_text(encoding="utf-8")
    assert "only Edit on" in text
    assert str(progress_path(run_log).resolve()) in text
    assert "→ Edit" in text


def test_fork_progress_note_no_edit_reports_noop(tmp_path, monkeypatch):
    from langbridge_code.agents.common import fork as fork_mod

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    path = Path(fork_mod.__file__)
    spec = importlib.util.spec_from_file_location("_fork_real", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setattr(fork_mod, "fork_progress_note", mod.fork_progress_note)

    def fake_create(api_key, model, messages, **kwargs):
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "nothing to add"}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        fake_create,
    )

    run_log = create_artifact_session("Progress noop")
    result = fork_mod.fork_progress_note(
        "key",
        "model",
        [{"role": "user", "content": "hi"}],
        run_log_path=run_log,
        tool_schemas=[{"type": "function", "name": "Edit"}],
    )
    assert "no file changes" in result
