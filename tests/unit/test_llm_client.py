import json

import pytest
from openai import RateLimitError

from langbridge_code.llm.client import (
    ApiQuotaExceeded,
    create_model_response,
    format_api_error,
    from_chat_message,
    quota_exceeded_message,
    rate_limit_is_non_retryable,
    to_chat_messages,
    to_chat_tools,
)


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _Fn(name, arguments)


class _Message:
    def __init__(self, content=None, tool_calls=None, reasoning=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning


def test_to_chat_messages_with_tool_roundtrip():
    agent_input = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "read README"},
        {
            "type": "function_call",
            "name": "read_file",
            "call_id": "call_1",
            "arguments": json.dumps({"path": "README.md", "purpose": "inspect docs"}),
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "# Title"},
        {"role": "user", "content": "thanks"},
    ]

    messages = to_chat_messages(agent_input)

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[3]["role"] == "tool"
    assert messages[3]["content"] == "# Title"
    assert messages[4]["role"] == "user"


def test_to_chat_messages_accepts_pending_placeholder_then_background_event():
    agent_input = [
        {"role": "user", "content": "run both"},
        {
            "type": "function_call",
            "name": "agent_worker",
            "call_id": "fast",
            "arguments": "{}",
        },
        {
            "type": "function_call",
            "name": "agent_worker",
            "call_id": "slow",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "fast", "output": "fast done"},
        {"type": "function_call_output", "call_id": "slow", "output": "still running"},
        {
            "role": "user",
            "content": "<background_tool_results>slow done</background_tool_results>",
        },
    ]

    messages = to_chat_messages(agent_input)

    assistant = messages[1]
    assert [call["id"] for call in assistant["tool_calls"]] == ["fast", "slow"]
    assert [message["tool_call_id"] for message in messages[2:4]] == ["fast", "slow"]
    assert messages[4]["role"] == "user"
    assert "slow done" in messages[4]["content"]


def test_to_chat_messages_preserves_reasoning_content_for_tool_calls():
    agent_input = [
        {"role": "user", "content": "fix the bug"},
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "I should grep first."}],
        },
        {
            "type": "function_call",
            "name": "grep",
            "call_id": "call_1",
            "arguments": json.dumps({"pattern": "bug"}),
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "hit"},
    ]

    messages = to_chat_messages(agent_input)

    assert messages[1]["role"] == "assistant"
    assert messages[1]["reasoning_content"] == "I should grep first."
    assert messages[1]["tool_calls"][0]["function"]["name"] == "grep"
    assert messages[2]["role"] == "tool"


def test_to_chat_messages_preserves_reasoning_content_for_final_reply():
    agent_input = [
        {"role": "user", "content": "done?"},
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Tests passed."}],
        },
        {"role": "assistant", "content": "Fixed."},
    ]

    messages = to_chat_messages(agent_input)

    assert messages[1]["role"] == "assistant"
    assert messages[1]["reasoning_content"] == "Tests passed."
    assert messages[1]["content"] == "Fixed."
    assert "tool_calls" not in messages[1]


def test_from_chat_message_maps_tool_calls():
    message = _Message(
        tool_calls=[_ToolCall("call_9", "bash", json.dumps({"command": "pytest -q"}))],
    )
    output = from_chat_message(message)

    assert output[0]["type"] == "function_call"
    assert output[0]["name"] == "bash"
    assert output[0]["call_id"] == "call_9"


def test_to_chat_tools_wraps_function_schema():
    tools = to_chat_tools([
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ])

    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "read_file"


def _rate_limit_error(message: str) -> RateLimitError:
    error = RateLimitError.__new__(RateLimitError)
    Exception.__init__(error, message)
    return error


def test_rate_limit_is_non_retryable_for_tpd():
    error = _rate_limit_error(
        "organization TPD rate limit, current: 1500012, limit: 1500000"
    )
    assert rate_limit_is_non_retryable(error) is True


def test_rate_limit_is_retryable_for_rpm():
    error = _rate_limit_error("Too many requests per minute")
    assert rate_limit_is_non_retryable(error) is False


def test_create_model_response_fails_fast_on_tpd(monkeypatch):
    error = _rate_limit_error("organization TPD rate limit")
    client = type("Client", (), {})()
    client.chat = type("Chat", (), {})()
    client.chat.completions = type("Completions", (), {})()
    client.chat.completions.create = lambda **_kwargs: (_ for _ in ()).throw(error)
    client.responses = None

    monkeypatch.setattr("langbridge_code.llm.client.make_client", lambda _key: client)
    monkeypatch.setattr("langbridge_code.llm.client.uses_responses_api", lambda: False)
    sleeps = []
    monkeypatch.setattr("langbridge_code.llm.client.time.sleep", lambda s: sleeps.append(s))

    with pytest.raises(ApiQuotaExceeded, match="daily token quota"):
        create_model_response("key", "kimi-k2.7-code", [{"role": "user", "content": "hi"}])

    assert sleeps == []


def _fake_chat_client(captured):
    class _Message:
        content = "ok"
        tool_calls = None
        reasoning_content = "think"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    def create(**kwargs):
        captured.update(kwargs)
        return _Response()

    client = type("Client", (), {})()
    client.chat = type("Chat", (), {})()
    client.chat.completions = type("Completions", (), {})()
    client.chat.completions.create = create
    return client


def _patch_chat_provider(monkeypatch, client, provider):
    monkeypatch.setattr("langbridge_code.llm.client.make_client", lambda _key: client)
    monkeypatch.setattr("langbridge_code.llm.client.uses_responses_api", lambda: False)
    monkeypatch.setattr("langbridge_code.settings.API_STREAMING_ENABLED", False)
    monkeypatch.setattr("langbridge_code.settings.API_PROVIDER", provider)


def test_create_model_response_enables_moonshot_thinking(monkeypatch):
    captured = {}
    _patch_chat_provider(monkeypatch, _fake_chat_client(captured), "moonshot")

    data = create_model_response("key", "kimi-k2.7-code", [{"role": "user", "content": "hi"}])

    assert captured["extra_body"]["thinking"] == {"type": "enabled", "keep": "all"}
    assert data["output"][0]["type"] == "reasoning"


def test_create_model_response_enables_deepseek_thinking(monkeypatch):
    captured = {}
    _patch_chat_provider(monkeypatch, _fake_chat_client(captured), "deepseek")

    data = create_model_response("key", "deepseek-v4-flash", [{"role": "user", "content": "hi"}])

    assert captured["extra_body"]["thinking"] == {"type": "enabled"}
    assert data["output"][0]["type"] == "reasoning"


def test_format_api_error_for_quota():
    message = format_api_error(
        ApiQuotaExceeded(quota_exceeded_message(_rate_limit_error("TPD")))
    )
    assert "daily token quota" in message.lower()
