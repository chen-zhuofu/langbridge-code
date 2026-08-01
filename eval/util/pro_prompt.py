"""Canonical problem formatting and audit metadata for public SWE-bench runs."""

from __future__ import annotations

import hashlib


VERIFIED_PROMPT_PROTOCOL_VERSION = "swebench-problem-statement-v1"
PRO_PROMPT_PROTOCOL_VERSION = "scale-swebench-pro-problem-requirements-interface-v1"

_PROMPT_FIELDS = {
    "verified": ("problem_statement",),
    "pro": ("problem_statement", "requirements", "interface"),
}


def format_problem_statement(instance: dict, *, difficulty: str) -> str:
    """Return the exact task text supplied to the agent.

    The Pro branch intentionally mirrors Scale's ``create_problem_statement``
    helper without trimming, decoding, or otherwise normalizing any field.
    """
    if difficulty == "verified":
        return instance["problem_statement"]
    if difficulty == "pro":
        problem_statement = instance["problem_statement"]
        requirement = instance["requirements"]
        interface = instance["interface"]
        return f"""{problem_statement}

Requirements:
{requirement}

New interfaces introduced:
{interface}"""
    raise ValueError(f"unsupported public eval difficulty: {difficulty}")


def prompt_protocol(difficulty: str) -> dict[str, object]:
    """Return stable protocol metadata suitable for a run checkpoint."""
    if difficulty not in _PROMPT_FIELDS:
        raise ValueError(f"unsupported public eval difficulty: {difficulty}")
    version = (
        PRO_PROMPT_PROTOCOL_VERSION
        if difficulty == "pro"
        else VERIFIED_PROMPT_PROTOCOL_VERSION
    )
    return {
        "version": version,
        "fields": list(_PROMPT_FIELDS[difficulty]),
    }


def prompt_sha256(instance: dict, *, difficulty: str) -> str:
    text = format_problem_statement(instance, difficulty=difficulty)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
