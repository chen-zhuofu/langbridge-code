import json

from langbridge_code.persistence import context as context_module


def make_record(turn_id, user, assistant, tool_output="", *, tool_name="read_file"):
    call_id = f"call_{turn_id}"
    step = {"step": 0}
    if tool_output:
        arguments = {"path": f"file_{turn_id}.py"} if tool_name == "read_file" else {"pattern": "needle"}
        step["output"] = [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": tool_name,
                "arguments": json.dumps(arguments),
            },
            {"type": "function_call_output", "call_id": call_id, "output": tool_output},
        ]
    return {
        "turn_id": turn_id,
        "user": user,
        "input": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": user},
        ],
        "steps": [step],
        "assistant": assistant,
    }


def test_restore_session_messages_compacts_large_sessions(monkeypatch):
    records = [
        make_record(1, "old task", "old answer", "old output " * 200, tool_name="grep"),
        make_record(2, "recent task", "recent answer", "recent output"),
    ]
    monkeypatch.setattr(context_module, "COMPACT_LOOP_FRACTION", 0.01)
    monkeypatch.setattr(context_module, "COMPACT_TOOL_STEPS_KEEP", 1)

    messages = context_module.restore_session_messages(records, max_context_tokens=10000)

    assert messages[0] == {"role": "system", "content": "system"}
    outputs = [item for item in messages if item.get("type") == "function_call_output"]
    assert outputs
    assert any(context_module.is_cleared_output(item["output"]) for item in outputs)
    assert {"role": "user", "content": "recent task"} in messages
    assert {"role": "assistant", "content": "recent answer"} in messages


def test_restore_session_messages_keeps_small_sessions_raw(monkeypatch):
    records = [make_record(1, "small task", "small answer", "small output")]
    monkeypatch.setattr(context_module, "COMPACT_LOOP_FRACTION", 0.99)

    messages = context_module.restore_session_messages(records, max_context_tokens=10000)

    outputs = [item for item in messages if item.get("type") == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["output"] == "small output"
    assert {"role": "user", "content": "small task"} in messages
    assert {"role": "assistant", "content": "small answer"} in messages
