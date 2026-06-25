from langbridge_cli import context as context_module


def make_record(turn_id, user, assistant, tool_output=""):
    return {
        "turn_id": turn_id,
        "user": user,
        "input": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": user},
        ],
        "steps": [
            {
                "step": 0,
                "action": [{"call_id": f"call_{turn_id}", "name": "read_file", "arguments": {"path": "x"}}],
                "observation": [{"call_id": f"call_{turn_id}", "output": tool_output}],
            }
        ],
        "assistant": assistant,
    }


def test_restore_session_messages_compacts_old_records(monkeypatch):
    records = [
        make_record(1, "old task", "old answer", "old output " * 200),
        make_record(2, "recent task", "recent answer", "recent output"),
    ]
    monkeypatch.setattr(context_module, "COMPACT_WHEN_TOKENS_OVER", 1)
    recent_tokens = context_module.estimate_tokens(context_module.records_to_messages([records[-1]]))
    monkeypatch.setattr(context_module, "RECENT_CONTEXT_TOKENS", recent_tokens + 1)

    messages = context_module.restore_session_messages(records)

    assert messages[0] == {"role": "system", "content": "system"}
    assert any(
        message.get("content", "").startswith("Older session summary:")
        for message in messages
        if message.get("role") == "assistant"
    )
    assert {"role": "user", "content": "recent task"} in messages
    assert {"role": "assistant", "content": "recent answer"} in messages


def make_reasoning_record(turn_id, user, assistant, tool_output):
    record = make_record(turn_id, user, assistant, tool_output)
    record["steps"][0]["reasoning"] = [{"type": "reasoning", "summary": []}]
    return record


def test_compacted_messages_truncate_stale_tool_outputs(monkeypatch):
    records = [
        make_reasoning_record(turn_id, f"task {turn_id}", f"answer {turn_id}", f"output {turn_id} " * 200)
        for turn_id in range(1, 5)
    ]
    monkeypatch.setattr(context_module, "RECENT_CONTEXT_TOKENS", 10**9)

    messages = context_module.restore_compacted_session_messages(records)

    outputs = [item for item in messages if item.get("type") == "function_call_output"]
    assert len(outputs) == 4
    for item in outputs[:2]:
        assert len(item["output"]) <= context_module.STALE_TOOL_OUTPUT_CHARS + len("...")
    for item in outputs[2:]:
        assert len(item["output"]) > context_module.STALE_TOOL_OUTPUT_CHARS


def test_restore_session_messages_keeps_small_sessions_raw(monkeypatch):
    records = [make_record(1, "small task", "small answer")]
    monkeypatch.setattr(context_module, "COMPACT_WHEN_TOKENS_OVER", 100_000)

    messages = context_module.restore_session_messages(records)

    assert not any(
        message.get("content", "").startswith("Older session summary:")
        for message in messages
        if message.get("role") == "assistant"
    )
    assert {"role": "user", "content": "small task"} in messages
    assert {"role": "assistant", "content": "small answer"} in messages
