"""Context budget: model window × fraction, surfaced to every agent."""
from __future__ import annotations

import json

from langbridge_code.context.prompt.context_budget_notice import (
    CONTEXT_BUDGET_BODY,
    CONTEXT_BUDGET_MARKER,
    CONTEXT_BUDGET_NEAR_LIMIT,
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


def strip_context_budget_notice(content: str) -> str:
    idx = content.find(CONTEXT_BUDGET_MARKER)
    if idx == -1:
        return content
    return content[:idx].rstrip()


def sync_context_budget_notice(messages, model: str, *, base_system_prompt: str | None = None) -> None:
    """Refresh the context budget line on the leading system message."""
    if not messages or messages[0].get("role") != "system":
        return
    base = base_system_prompt if base_system_prompt is not None else strip_context_budget_notice(
        str(messages[0].get("content", ""))
    )
    line = format_context_budget_line(messages, model)
    messages[0]["content"] = f"{base.rstrip()}{CONTEXT_BUDGET_MARKER}\n{line}"


def prepare_agent_messages(messages, model: str, *, base_system_prompt: str | None = None) -> int:
    """Inject fresh budget stats and return the token limit for this model."""
    sync_context_budget_notice(messages, model, base_system_prompt=base_system_prompt)
    return context_budget_tokens(model)


def format_status_context_line(messages, model: str, *, label: str | None = None) -> str:
    snap = context_budget_snapshot(messages, model)
    prefix = f"{label} context" if label else "context"
    return (
        f"{prefix} {snap['used_pct_of_budget']:.1f}% "
        f"({format_token_count(snap['used_tokens'])}/"
        f"{format_token_count(snap['budget_tokens'])}, "
        f"window {format_token_count(snap['window_tokens'])})"
    )
