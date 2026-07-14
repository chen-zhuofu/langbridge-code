"""Compress older raw rounds (plus prior compact prose) into one handoff block."""
from __future__ import annotations

import json

from langbridge_code.context.prompt.compact_prose import COMPACT_PROSE_SYSTEM
from langbridge_code.llm.parse import extract_output_text, truncate_text
from langbridge_code.settings import COMPACT_PROSE_TARGET_CHARS

COMPACT_PROSE_PREFIX = "[CONTEXT_COMPACT]\n"


def _serialize_rounds(rounds: list[list[dict]]) -> str:
    payload = [
        {"round": index + 1, "messages": round_messages}
        for index, round_messages in enumerate(rounds)
    ]
    return truncate_text(json.dumps(payload, ensure_ascii=False, indent=2), 120_000)


def compact_rounds_to_prose(
    api_key: str,
    model: str,
    *,
    compact_prose: str | None,
    rounds: list[list[dict]],
    label: str = "prose compact",
) -> str:
    """Merge existing compact prose and raw rounds into one prose block."""
    from langbridge_code.llm.client import create_model_response

    if not rounds:
        return compact_prose or ""

    parts: list[str] = []
    if compact_prose and compact_prose.strip():
        parts.append("Existing compact context:\n" + compact_prose.strip())
    parts.append("Conversation rounds to fold in:\n" + _serialize_rounds(rounds))

    prompt = (
        "Compress the following into one compact handoff note:\n\n"
        + "\n\n---\n\n".join(parts)
    )
    data = create_model_response(
        api_key,
        model,
        [
            {"role": "system", "content": COMPACT_PROSE_SYSTEM},
            {"role": "user", "content": truncate_text(prompt, 120_000)},
        ],
        label=label,
    )
    text = extract_output_text(data.get("output", [])).strip()
    if not text:
        return compact_prose or ""
    return truncate_text(text, COMPACT_PROSE_TARGET_CHARS)
