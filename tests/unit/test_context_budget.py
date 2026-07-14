from langbridge_code.context.common.budget import (
    context_budget_snapshot,
    context_budget_tokens,
    format_context_budget_line,
    prepare_agent_messages,
    strip_context_budget_notice,
)
from langbridge_code.llm.model_context import model_context_window


def test_model_context_window_for_default_kimi():
    assert model_context_window("kimi-k2.7-code") == 262_144


def test_context_budget_uses_fraction():
    assert context_budget_tokens("kimi-k2.7-code") == int(262_144 * 0.4)


def test_prepare_agent_messages_injects_budget_notice():
    messages = [{"role": "system", "content": "You are a test agent."}]
    budget = prepare_agent_messages(messages, "kimi-k2.7-code")
    assert budget == int(262_144 * 0.4)
    assert "Compact threshold" in messages[0]["content"]
    assert "no hard context stop" in messages[0]["content"]
    assert "262,144 tokens" in messages[0]["content"]


def test_strip_context_budget_notice():
    content = "Base prompt.\n\n---\nContext status (updated each step):\nline"
    assert strip_context_budget_notice(content) == "Base prompt."


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
