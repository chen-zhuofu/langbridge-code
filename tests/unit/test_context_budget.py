from langbridge_code.context.common.budget import (
    context_budget_snapshot,
    context_budget_tokens,
    format_context_budget_line,
    messages_with_budget_notice,
    prepare_agent_messages,
)
from langbridge_code.llm.model_context import model_context_window


def test_model_context_window_for_default_kimi():
    assert model_context_window("kimi-k2.7-code") == 262_144


def test_context_budget_uses_fraction():
    assert context_budget_tokens("kimi-k2.7-code") == int(262_144 * 0.4)


def test_prepare_agent_messages_keeps_system_prompt_stable():
    """Prefix caching: the system prompt must stay byte-identical across steps."""
    messages = [{"role": "system", "content": "You are a test agent."}]
    budget = prepare_agent_messages(
        messages, "kimi-k2.7-code", base_system_prompt="You are a test agent."
    )
    assert budget == int(262_144 * 0.4)
    assert messages[0]["content"] == "You are a test agent."

    messages.append({"role": "user", "content": "hello"})
    messages[0]["content"] = "You are a test agent.\n\ndrifted"
    prepare_agent_messages(
        messages, "kimi-k2.7-code", base_system_prompt="You are a test agent."
    )
    assert messages[0]["content"] == "You are a test agent."


def test_messages_with_budget_notice_appends_transient_tail():
    messages = [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "hello"},
    ]
    request = messages_with_budget_notice(messages, "kimi-k2.7-code")

    # Stored transcript untouched; request gets one extra trailing message.
    assert len(messages) == 2
    assert len(request) == 3
    assert request[:2] == messages
    tail = request[-1]["content"]
    assert request[-1]["role"] == "user"
    assert "[CONTEXT_STATUS]" in tail
    assert "Compact threshold" in tail
    assert "no hard context stop" in tail
    assert "262,144 tokens" in tail


def test_context_budget_snapshot_tracks_usage():
    messages = [
        {"role": "system", "content": "x" * 400},
        {"role": "user", "content": "hello"},
    ]
    snap = context_budget_snapshot(messages, "kimi-k2.7-code")
    assert snap["window_tokens"] == 262_144
    assert snap["budget_tokens"] == int(262_144 * 0.4)
    assert snap["used_tokens"] > 0
    assert "Current transcript size:" in format_context_budget_line(messages, "kimi-k2.7-code")
