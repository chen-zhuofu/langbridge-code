"""Context budget: model window × fraction, surfaced to every agent."""
from __future__ import annotations

import json

from langbridge_code.prompt.context.context_budget_notice import (
    CONTEXT_BUDGET_BODY,
    CONTEXT_BUDGET_NEAR_LIMIT,
    CONTEXT_BUDGET_NOTICE_PREFIX,
)
from langbridge_code.llm.model_context import format_token_count, model_context_window
from langbridge_code.settings import CONTEXT_WINDOW_MAX_FRACTION


def estimate_tokens(value):
    return len(json.dumps(value, ensure_ascii=False)) // 4


def context_budget_tokens(model: str, *, fraction: float | None = None) -> int:
    window = model_context_window(model)
    pct = CONTEXT_WINDOW_MAX_FRACTION if fraction is None else fraction
    return max(1, int(window * pct))


def context_usage(messages) -> int:
    return estimate_tokens(messages)


def context_budget_snapshot(messages, model: str, *, fraction: float | None = None) -> dict:
    window = model_context_window(model)
    budget = context_budget_tokens(model, fraction=fraction)
    used = context_usage(messages)
    pct = CONTEXT_WINDOW_MAX_FRACTION if fraction is None else fraction
    return {
        "model": model,
        "window_tokens": window,
        "budget_tokens": budget,
        "used_tokens": used,
        "budget_fraction": pct,
        "used_pct_of_budget": round(100 * used / budget, 1) if budget else 0.0,
        "used_pct_of_window": round(100 * used / window, 1) if window else 0.0,
    }


def format_context_budget_line(messages, model: str) -> str:
    snap = context_budget_snapshot(messages, model)
    pct = int(snap["budget_fraction"] * 100)
    lines = [
        f"Model context window: {snap['window_tokens']:,} tokens.",
        f"Current transcript size: {snap['used_tokens']:,} tokens "
        f"({snap['used_pct_of_window']}% of model window).",
        f"Compact threshold: {snap['budget_tokens']:,} tokens ({pct}% of model window) "
        f"— currently {snap['used_pct_of_budget']}% of that threshold.",
        CONTEXT_BUDGET_BODY,
    ]
    if snap["used_pct_of_budget"] >= 75:
        lines.append(CONTEXT_BUDGET_NEAR_LIMIT)
    return " ".join(lines)


def _ensure_stable_system_prompt(messages, *, base_system_prompt: str | None = None) -> None:
    """Keep the leading system message byte-identical across steps.

    Budget stats used to be rewritten into the system prompt every step, which
    defeated provider prefix caching. Now stats ride on the request tail only;
    when callers pass the canonical base prompt, reinstate it if it drifted.
    """
    if not messages or messages[0].get("role") != "system":
        return
    if base_system_prompt is None:
        return
    base = base_system_prompt.rstrip()
    if str(messages[0].get("content", "")) != base:
        messages[0]["content"] = base


def prepare_agent_messages(messages, model: str, *, base_system_prompt: str | None = None) -> int:
    """Keep the prompt prefix cache-stable and return the token budget."""
    _ensure_stable_system_prompt(messages, base_system_prompt=base_system_prompt)
    return context_budget_tokens(model)


def budget_notice_message(messages, model: str) -> dict:
    """One transient user message with current budget stats for the request tail."""
    line = format_context_budget_line(messages, model)
    return {"role": "user", "content": f"{CONTEXT_BUDGET_NOTICE_PREFIX}\n{line}"}


def messages_with_budget_notice(messages, model: str) -> list:
    """Copy of ``messages`` with budget stats appended as the last message.

    The stats change every step, so they must never enter the stored
    transcript or the (cache-relevant) prompt prefix — only the request tail.
    """
    return [*messages, budget_notice_message(messages, model)]


def format_status_context_line(messages, model: str, *, label: str | None = None) -> str:
    snap = context_budget_snapshot(messages, model)
    prefix = f"{label} context" if label else "context"
    return (
        f"{prefix} {snap['used_pct_of_budget']:.1f}% "
        f"({format_token_count(snap['used_tokens'])}/"
        f"{format_token_count(snap['budget_tokens'])}, "
        f"window {format_token_count(snap['window_tokens'])})"
    )
