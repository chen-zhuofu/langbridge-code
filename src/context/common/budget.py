"""Context budget: fixed compact threshold, surfaced to every agent."""
from __future__ import annotations

import json

from langbridge_code.prompt.context import (
    CONTEXT_BUDGET_BODY,
    CONTEXT_BUDGET_NEAR_LIMIT,
    CONTEXT_BUDGET_NOTICE_PREFIX,
)
from langbridge_code.llm.model_context import format_token_count, model_context_window
from langbridge_code.settings import COMPACT_THRESHOLD_TOKENS


def estimate_tokens(value):
    return len(json.dumps(value, ensure_ascii=False)) // 4


def context_budget_tokens(model: str, *, fraction: float | None = None) -> int:
    """Return the compact/budget threshold in tokens.

    Threshold is fixed via COMPACT_THRESHOLD_TOKENS (default 100k), independent
    of the model window. ``fraction`` is ignored (kept for call-site compat).
    """
    del model, fraction
    return max(1, int(COMPACT_THRESHOLD_TOKENS))


def context_usage(messages) -> int:
    return estimate_tokens(messages)


def context_budget_snapshot(messages, model: str, *, fraction: float | None = None) -> dict:
    window = model_context_window(model)
    budget = context_budget_tokens(model, fraction=fraction)
    used = context_usage(messages)
    return {
        "model": model,
        "window_tokens": window,
        "budget_tokens": budget,
        "used_tokens": used,
        "budget_fraction": None,
        "used_pct_of_budget": round(100 * used / budget, 1) if budget else 0.0,
        "used_pct_of_window": round(100 * used / window, 1) if window else None,
    }


def format_context_budget_line(messages, model: str) -> str:
    snap = context_budget_snapshot(messages, model)
    if snap["window_tokens"]:
        window_line = f"Model context window: {snap['window_tokens']:,} tokens."
        usage_line = (
            f"Current transcript size: {snap['used_tokens']:,} tokens "
            f"({snap['used_pct_of_window']}% of model window)."
        )
    else:
        window_line = "Model context window: unknown."
        usage_line = f"Current transcript size: {snap['used_tokens']:,} tokens."
    lines = [
        window_line,
        usage_line,
        f"Compact threshold: {snap['budget_tokens']:,} tokens (fixed) "
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
    window = format_token_count(snap["window_tokens"])
    window_part = f", window {window}" if window else ", window "
    return (
        f"{prefix} {snap['used_pct_of_budget']:.1f}% "
        f"({format_token_count(snap['used_tokens'])}/"
        f"{format_token_count(snap['budget_tokens'])}{window_part})"
    )
