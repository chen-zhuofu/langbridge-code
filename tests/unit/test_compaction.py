import json
import sys
import types

from langbridge_code.persistence import context as context_module
from langbridge_code.persistence.context import (
    CLEARED_PREFIX,
    HISTORY_SUMMARY_PREFIX,
    clear_old_tool_outputs,
    protected_read_file_output_indices,
)


def _read_round(call_id: str, path: str, content: str) -> list[dict]:
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "read_file",
            "arguments": f'{{"path": "{path}"}}',
        },
        {"type": "function_call_output", "call_id": call_id, "output": content},
    ]


def _grep_round(call_id: str, content: str) -> list[dict]:
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": "grep",
            "arguments": '{"query": "foo"}',
        },
        {"type": "function_call_output", "call_id": call_id, "output": content},
    ]


def _tool_round(messages, name, arguments, output, *, call_id="c1"):
    messages.extend(
        [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            },
            {"type": "function_call_output", "call_id": call_id, "output": output},
        ]
    )


def test_clear_old_tool_outputs_keeps_last_five_read_file_outputs():
    messages: list[dict] = [{"role": "system", "content": "sys"}]
    for index in range(7):
        messages.extend(_read_round(f"read-{index}", f"a{index}.py", f"content-{index}"))

    cleared = clear_old_tool_outputs(messages, keep_recent_steps=0, keep_recent_read_outputs=5)
    outputs = [m["output"] for m in messages if m.get("type") == "function_call_output"]

    assert cleared == 2
    assert outputs[0].startswith(CLEARED_PREFIX)
    assert outputs[1].startswith(CLEARED_PREFIX)
    assert outputs[2:] == ["content-2", "content-3", "content-4", "content-5", "content-6"]


def test_clear_old_tool_outputs_still_clears_other_tools():
    messages: list[dict] = [{"role": "system", "content": "sys"}]
    messages.extend(_grep_round("grep-0", "match-0"))
    messages.extend(_grep_round("grep-1", "match-1"))
    messages.extend(_grep_round("grep-2", "match-2"))
    messages.extend(_read_round("read-0", "x.py", "full-content"))

    cleared = clear_old_tool_outputs(messages, keep_recent_steps=0, keep_recent_read_outputs=5)
    grep_outputs = [
        m["output"]
        for m in messages
        if m.get("type") == "function_call_output" and m["call_id"].startswith("grep")
    ]
    read_outputs = [
        m["output"] for m in messages if m.get("type") == "function_call_output" and m["call_id"] == "read-0"
    ]

    assert cleared == 3
    assert all(output.startswith(CLEARED_PREFIX) for output in grep_outputs)
    assert read_outputs == ["full-content"]


def test_protected_read_file_output_indices():
    messages: list[dict] = []
    for index in range(6):
        messages.extend(_read_round(f"read-{index}", f"f{index}.py", f"body-{index}"))

    protected = protected_read_file_output_indices(messages, keep_recent_reads=5)
    outputs = [index for index, m in enumerate(messages) if m.get("type") == "function_call_output"]

    assert protected == set(outputs[1:])


def test_clear_old_tool_outputs_keeps_recent_rounds():
    messages = [{"role": "system", "content": "sys"}]
    _tool_round(messages, "grep", {"pattern": "old"}, "OLD CONTENT", call_id="c1")
    _tool_round(messages, "grep", {"pattern": "mid"}, "MID CONTENT", call_id="c2")
    _tool_round(messages, "read_file", {"path": "new.py"}, "NEW CONTENT", call_id="c3")

    cleared = context_module.clear_old_tool_outputs(messages, keep_recent_steps=1)
    assert cleared == 2
    assert context_module.is_cleared_output(messages[2]["output"])
    assert context_module.is_cleared_output(messages[4]["output"])
    assert messages[6]["output"] == "NEW CONTENT"


def test_maybe_compact_messages_triggers_over_threshold(monkeypatch):
    monkeypatch.setattr(context_module, "COMPACT_LOOP_FRACTION", 0.01)
    messages = [{"role": "system", "content": "x" * 5000}]
    _tool_round(messages, "grep", {"pattern": "a"}, "A" * 8000, call_id="c1")
    _tool_round(messages, "read_file", {"path": "b.py"}, "B" * 8000, call_id="c2")

    result = context_module.maybe_compact_messages(
        messages,
        max_context_tokens=10000,
        keep_recent_steps=1,
    )

    assert result["compacted"] is True
    assert result["cleared"] == 1
    assert messages[-1]["output"] == "B" * 8000


def test_cleared_tool_summary_read_file():
    summary = context_module.cleared_tool_summary("read_file", {"path": "x.py"}, "line1\nline2\n")
    assert summary.startswith(CLEARED_PREFIX)
    assert "x.py" in summary


def test_cleared_tool_summary_bash():
    summary = context_module.cleared_tool_summary("bash", {"command": "uv add pytest"}, "installed\n")
    assert summary.startswith(CLEARED_PREFIX)
    assert "uv add pytest" in summary


def test_summarize_history_with_llm(monkeypatch):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Add widget API"},
        {"role": "assistant", "content": "I'll implement the widget handler."},
    ]

    def fake_response(api_key, model, agent_input, **kwargs):
        assert kwargs.get("label") == "test compaction"
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "- Task: widget API\n- Decision: use handler pattern"}],
                }
            ]
        }

    fake_client = types.ModuleType("langbridge_code.llm.client")
    fake_client.create_model_response = fake_response
    monkeypatch.setitem(sys.modules, "langbridge_code.llm.client", fake_client)

    summary = context_module.summarize_history_with_llm("key", "model", messages, label="test compaction")
    assert "widget API" in summary
    assert "handler" in summary


def test_sync_history_summary_skips_without_llm():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    context_module.sync_history_summary(messages)
    assert len(messages) == 2


def test_sync_history_summary_uses_llm_when_configured(monkeypatch):
    monkeypatch.setattr(context_module, "COMPACT_USE_LLM", True)
    monkeypatch.setattr(
        context_module,
        "summarize_history_with_llm",
        lambda api_key, model, messages, label="compaction": "LLM summary bullets",
    )
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    context_module.sync_history_summary(messages, api_key="k", model="m")
    assert messages[2]["content"] == HISTORY_SUMMARY_PREFIX + "LLM summary bullets"
