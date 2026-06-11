from langbridge_cli import session as session_module


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
    monkeypatch.setattr(session_module, "COMPACT_WHEN_TOKENS_OVER", 1)
    recent_tokens = session_module.estimate_tokens(session_module.records_to_messages([records[-1]]))
    monkeypatch.setattr(session_module, "RECENT_CONTEXT_TOKENS", recent_tokens + 1)

    messages = session_module.restore_session_messages(records)

    assert messages[0] == {"role": "system", "content": "system"}
    assert any(
        message.get("content", "").startswith("Older session summary:")
        for message in messages
        if message.get("role") == "assistant"
    )
    assert {"role": "user", "content": "recent task"} in messages
    assert {"role": "assistant", "content": "recent answer"} in messages


def test_restore_session_messages_keeps_small_sessions_raw(monkeypatch):
    records = [make_record(1, "small task", "small answer")]
    monkeypatch.setattr(session_module, "COMPACT_WHEN_TOKENS_OVER", 100_000)

    messages = session_module.restore_session_messages(records)

    assert not any(
        message.get("content", "").startswith("Older session summary:")
        for message in messages
        if message.get("role") == "assistant"
    )
    assert {"role": "user", "content": "small task"} in messages
    assert {"role": "assistant", "content": "small answer"} in messages
