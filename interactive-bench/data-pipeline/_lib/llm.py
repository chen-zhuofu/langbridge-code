"""Minimal OpenAI-compatible chat helper for intent / sim stages."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def api_key() -> str | None:
    return (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LB_OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


def _post_chat(base: str, key: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def _parse_json_content(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def chat_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
) -> dict[str, Any] | None:
    """Call chat completions expecting a JSON object in the assistant text.

    Returns parsed dict or None if no key / request fails.
    """
    key = api_key()
    if not key:
        return None
    model = model or os.environ.get("LB_INTERACTIVE_MODEL") or "gpt-4o-mini"
    base = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip(
        "/"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    payload = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    body = _post_chat(base, key, payload)
    if body is None:
        payload.pop("response_format", None)
        body = _post_chat(base, key, payload)
    if not body:
        return None
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return _parse_json_content(text)
