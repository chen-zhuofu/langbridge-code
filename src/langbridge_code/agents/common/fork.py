"""One-pass forks of a live agent context (prefix-cache friendly).

A fork reuses the agent's message list verbatim and appends one instruction,
so the provider can serve the shared prefix from cache. Used for progress
notes and memory extraction — a fresh LLM cannot read the raw traces
(uncompressed, far too large), but the live context already has everything.
"""
from __future__ import annotations


def fork_one_pass(api_key, model, messages: list[dict], instruction: str, *, label: str = "fork") -> str:
    from langbridge_code.llm.client import create_model_response
    from langbridge_code.llm.parse import extract_output_text

    forked = list(messages) + [{"role": "user", "content": instruction}]
    data = create_model_response(api_key, model, forked, label=label)
    return extract_output_text(data.get("output", [])).strip()
