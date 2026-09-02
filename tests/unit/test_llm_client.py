import json

import pytest
from openai import RateLimitError

from langbridge_code.llm.client import (
    ApiQuotaExceeded,
    create_model_response,
    format_api_error,
    from_chat_message,
    hit_max_output_tokens,
    quota_exceeded_message,
    rate_limit_is_non_retryable,
    resolve_max_output_tokens,
    supports_native_tool_search,
    to_chat_messages,
    to_chat_tools,
    uses_responses_api,
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
            "arguments": json.dumps({"path": "README.md", "description": "inspect docs"}),
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

    monkeypatch.setattr("langbridge_code.llm.client.make_client", lambda *a, **k: client)
    monkeypatch.setattr("langbridge_code.llm.client.uses_responses_api", lambda *a, **k: False)
    sleeps = []
    monkeypatch.setattr("langbridge_code.llm.client.time.sleep", lambda s: sleeps.append(s))

    with pytest.raises(ApiQuotaExceeded, match="daily token quota"):
        create_model_response("key", "kimi-k2.7-code", [{"role": "user", "content": "hi"}])

    assert sleeps == []


def _fake_chat_client(captured, *, finish_reasons=None):
    class _Message:
        content = "ok"
        tool_calls = None
        reasoning_content = "think"

    class _Choice:
        def __init__(self, finish_reason):
            self.message = _Message()
            self.finish_reason = finish_reason

    class _Response:
        def __init__(self, finish_reason):
            self.choices = [_Choice(finish_reason)]

    reasons = list(finish_reasons or [None])
    calls = {"n": 0}

    def create(**kwargs):
        captured.setdefault("calls", []).append(dict(kwargs))
        captured.update(kwargs)
        idx = min(calls["n"], len(reasons) - 1)
        calls["n"] += 1
        return _Response(reasons[idx])

    client = type("Client", (), {})()
    client.chat = type("Chat", (), {})()
    client.chat.completions = type("Completions", (), {})()
    client.chat.completions.create = create
    return client


def _patch_chat_provider(monkeypatch, client, provider):
    monkeypatch.setattr("langbridge_code.llm.client.make_client", lambda *a, **k: client)
    monkeypatch.setattr("langbridge_code.llm.client.uses_responses_api", lambda *a, **k: False)
    monkeypatch.setattr("langbridge_code.settings.API_STREAMING_ENABLED", False)
    monkeypatch.setattr("langbridge_code.settings.API_PROVIDER", provider)


def test_create_model_response_enables_moonshot_thinking(monkeypatch):
    captured = {}
    _patch_chat_provider(monkeypatch, _fake_chat_client(captured), "moonshot")

    data = create_model_response("key", "kimi-k2.7-code", [{"role": "user", "content": "hi"}])

    assert captured["extra_body"]["thinking"] == {"type": "enabled", "keep": "all"}
    assert data["output"][0]["type"] == "reasoning"


def test_create_model_response_uses_kimi_k3_reasoning_effort(monkeypatch):
    captured = {}
    _patch_chat_provider(monkeypatch, _fake_chat_client(captured), "moonshot")

    data = create_model_response("key", "kimi-k3", [{"role": "user", "content": "hi"}])

    assert captured["extra_body"] == {"reasoning_effort": "max"}
    assert "thinking" not in captured["extra_body"]
    assert data["output"][0]["type"] == "reasoning"


def test_create_model_response_uses_routed_kimi_code_model(monkeypatch):
    captured = {}
    _patch_chat_provider(monkeypatch, _fake_chat_client(captured), "moonshot")
    monkeypatch.setattr(
        "langbridge_code.settings.resolve_llm_route",
        lambda model, api_key: {
            "provider": "moonshot",
            "api_key": api_key,
            "base_url": "https://api.kimi.com/coding/v1",
            "model": "k3",
        },
    )

    create_model_response(
        "sk-kimi-test",
        "kimi-k3",
        [{"role": "user", "content": "hi"}],
    )

    assert captured["model"] == "k3"
    assert captured["extra_body"] == {"reasoning_effort": "max"}


def test_create_model_response_enables_deepseek_thinking(monkeypatch):
    captured = {}
    _patch_chat_provider(monkeypatch, _fake_chat_client(captured), "deepseek")

    data = create_model_response("key", "deepseek-v4-flash", [{"role": "user", "content": "hi"}])

    assert captured["extra_body"]["thinking"] == {"type": "enabled"}
    assert captured["extra_body"]["reasoning_effort"] == "max"
    assert data["output"][0]["type"] == "reasoning"


def test_create_model_response_can_use_low_budget_deepseek_classifier(monkeypatch):
    captured = {}
    _patch_chat_provider(monkeypatch, _fake_chat_client(captured), "deepseek")

    create_model_response(
        "key",
        "deepseek-v4-flash",
        [{"role": "user", "content": "classify"}],
        reasoning={"effort": "low"},
        max_output_tokens=64,
    )

    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert captured["max_tokens"] == 64


def test_create_model_response_routes_cross_provider(monkeypatch):
    """Explorer model on deepseek should call make_client with deepseek base_url."""
    captured = {}
    client = _fake_chat_client(captured)
    calls = []

    def fake_make_client(api_key, *, base_url=None):
        calls.append({"api_key": api_key, "base_url": base_url})
        return client

    monkeypatch.setattr("langbridge_code.llm.client.make_client", fake_make_client)
    monkeypatch.setattr("langbridge_code.llm.client.uses_responses_api", lambda *a, **k: False)
    monkeypatch.setattr("langbridge_code.settings.API_STREAMING_ENABLED", False)
    monkeypatch.setattr(
        "langbridge_code.settings.resolve_llm_route",
        lambda model, api_key=None: {
            "provider": "deepseek",
            "api_key": "sk-deep",
            "base_url": "https://api.deepseek.com",
        },
    )

    create_model_response(
        "sk-moon-session",
        "deepseek-v4-flash",
        [{"role": "user", "content": "hi"}],
    )

    assert calls == [{"api_key": "sk-deep", "base_url": "https://api.deepseek.com"}]
    assert captured["extra_body"]["thinking"] == {"type": "enabled"}
    assert captured["extra_body"]["reasoning_effort"] == "max"


def test_uses_responses_api_only_for_openai():
    assert uses_responses_api("openai") is True
    assert uses_responses_api("anthropic") is False
    assert uses_responses_api("moonshot") is False
    assert uses_responses_api("deepseek") is False


def test_native_tool_search_requires_openai_gpt_5_4_or_newer():
    assert supports_native_tool_search("gpt-5.4", "openai") is True
    assert supports_native_tool_search("gpt-5.6-sol", "openai") is True
    assert supports_native_tool_search("gpt-5.3", "openai") is False
    assert supports_native_tool_search("deepseek-v4-flash", "deepseek") is False


def test_create_model_response_uses_openai_xhigh_reasoning(monkeypatch):
    captured = {}

    class _Response:
        def model_dump(self, exclude_none=True):
            return {
                "output": [
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "think"}],
                    }
                ]
            }

    client = type("Client", (), {})()
    client.responses = type("Responses", (), {})()

    def create(**kwargs):
        captured.update(kwargs)
        return _Response()

    client.responses.create = create
    monkeypatch.setattr("langbridge_code.llm.client.make_client", lambda *a, **k: client)
    monkeypatch.setattr(
        "langbridge_code.settings.resolve_llm_route",
        lambda model, api_key=None: {
            "provider": "openai",
            "api_key": "sk-openai",
            "base_url": "https://api.openai.com/v1",
        },
    )

    data = create_model_response("key", "gpt-5.5", [{"role": "user", "content": "hi"}])

    assert captured["reasoning"] == {"effort": "xhigh", "summary": "auto"}
    assert data["output"][0]["type"] == "reasoning"


def test_create_model_response_uses_openai_max_reasoning_for_gpt_5_6(monkeypatch):
    captured = {}

    class _Response:
        def model_dump(self, exclude_none=True):
            return {
                "output": [
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "think"}],
                    }
                ]
            }

    client = type("Client", (), {})()
    client.responses = type("Responses", (), {})()

    def create(**kwargs):
        captured.update(kwargs)
        return _Response()

    client.responses.create = create
    monkeypatch.setattr("langbridge_code.llm.client.make_client", lambda *a, **k: client)
    monkeypatch.setattr(
        "langbridge_code.settings.resolve_llm_route",
        lambda model, api_key=None: {
            "provider": "openai",
            "api_key": "sk-openai",
            "base_url": "https://api.openai.com/v1",
        },
    )

    data = create_model_response("key", "gpt-5.6", [{"role": "user", "content": "hi"}])

    assert captured["reasoning"] == {"effort": "max", "summary": "auto"}
    assert data["output"][0]["type"] == "reasoning"


def test_create_model_response_routes_anthropic_chat(monkeypatch):
    """Anthropic uses OpenAI-compatible chat completions, not Responses API."""
    captured = {}
    client = _fake_chat_client(captured)
    calls = []
    responses_calls = []

    def fake_make_client(api_key, *, base_url=None):
        calls.append({"api_key": api_key, "base_url": base_url})
        return client

    client.responses = type("Responses", (), {})()
    client.responses.create = lambda **kwargs: responses_calls.append(kwargs) or (_ for _ in ()).throw(
        AssertionError("anthropic must not call responses.create")
    )

    monkeypatch.setattr("langbridge_code.llm.client.make_client", fake_make_client)
    monkeypatch.setattr("langbridge_code.settings.API_STREAMING_ENABLED", False)
    monkeypatch.setattr(
        "langbridge_code.settings.resolve_llm_route",
        lambda model, api_key=None: {
            "provider": "anthropic",
            "api_key": "sk-ant",
            "base_url": "https://api.anthropic.com/v1/",
        },
    )

    data = create_model_response(
        "sk-session",
        "claude-fable-5",
        [{"role": "user", "content": "hi"}],
    )

    assert calls == [{"api_key": "sk-ant", "base_url": "https://api.anthropic.com/v1/"}]
    assert responses_calls == []
    assert captured["extra_body"] == {"output_config": {"effort": "max"}}
    assert data["output"][0]["type"] == "reasoning"


def test_format_api_error_for_quota():
    message = format_api_error(
        ApiQuotaExceeded(quota_exceeded_message(_rate_limit_error("TPD")))
    )
    assert "daily token quota" in message.lower()


def test_resolve_max_output_tokens_default(monkeypatch):
    monkeypatch.delenv("LANGBRIDGE_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setattr("langbridge_code.settings.DEFAULT_MAX_OUTPUT_TOKENS", 8000)
    assert resolve_max_output_tokens() == (8000, False)


def test_resolve_max_output_tokens_env_override(monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_MAX_OUTPUT_TOKENS", "12000")
    assert resolve_max_output_tokens() == (12000, True)


def test_hit_max_output_tokens_reasons():
    assert hit_max_output_tokens({}, "length") is True
    assert hit_max_output_tokens({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}) is True
    assert hit_max_output_tokens({}, "stop") is False


def test_create_model_response_sends_max_tokens(monkeypatch):
    captured = {}
    _patch_chat_provider(monkeypatch, _fake_chat_client(captured), "moonshot")
    monkeypatch.delenv("LANGBRIDGE_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setattr("langbridge_code.settings.DEFAULT_MAX_OUTPUT_TOKENS", 8000)

    create_model_response("key", "kimi-k2.7-code", [{"role": "user", "content": "hi"}])

    assert captured["max_tokens"] == 8000


def test_create_model_response_escalates_max_tokens_once(monkeypatch):
    captured = {}
    client = _fake_chat_client(captured, finish_reasons=["length", "stop"])
    _patch_chat_provider(monkeypatch, client, "moonshot")
    monkeypatch.delenv("LANGBRIDGE_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setattr("langbridge_code.settings.DEFAULT_MAX_OUTPUT_TOKENS", 8000)
    monkeypatch.setattr("langbridge_code.settings.ESCALATED_MAX_OUTPUT_TOKENS", 64000)

    create_model_response("key", "kimi-k2.7-code", [{"role": "user", "content": "hi"}])

    assert [call["max_tokens"] for call in captured["calls"]] == [8000, 64000]
