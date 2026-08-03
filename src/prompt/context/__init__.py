"""Context budget notice prompts — bodies live in sibling ``*.md`` files."""
from __future__ import annotations

from langbridge_code.prompt.load import load_prompt

CONTEXT_BUDGET_NOTICE_PREFIX = load_prompt(
    "context/context_budget_notice_prefix.md"
).rstrip("\n")
CONTEXT_BUDGET_BODY = load_prompt("context/context_budget_body.md").rstrip("\n")
CONTEXT_BUDGET_NEAR_LIMIT = load_prompt(
    "context/context_budget_near_limit.md"
).rstrip("\n")

__all__ = [
    "CONTEXT_BUDGET_BODY",
    "CONTEXT_BUDGET_NEAR_LIMIT",
    "CONTEXT_BUDGET_NOTICE_PREFIX",
]
