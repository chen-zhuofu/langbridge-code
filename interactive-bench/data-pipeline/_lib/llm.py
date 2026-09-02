"""Minimal OpenAI-compatible chat helper for intent / sim stages.

Credentials resolve in order:
1. Explicit ``base_url`` / ``model`` args when provided by the caller
2. ``LB_INTERACTIVE_MODEL`` / ``OPENAI_BASE_URL`` env overrides
3. LangBridge ``~/.langbridge/config.json`` (active provider key + base_url + model)
4. Classic env keys: ``OPENAI_API_KEY`` / ``LB_OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``
   (and other provider env vars via LangBridge settings)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

RETRIES = 3
BACKOFF_SEC = 2.0
# Retrying these status codes cannot help: bad key, unknown model, malformed body.
_FATAL_STATUS = frozenset({400, 401, 403, 404, 422})


class LLMError(RuntimeError):
    """A chat call failed. ``fatal`` marks errors a retry cannot fix."""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


def api_key() -> str | None:
    """Legacy helper: first env key only (no LangBridge config). Prefer ``resolve_chat_route``."""
    return (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LB_OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


def _load_langbridge_settings():
    """Import ``langbridge_code.settings``, tolerating a broken editable install.

    macOS can set the ``hidden`` flag on venv ``.pth`` files (iCloud-synced
    Desktop), which makes Python 3.13 silently skip them — so the normal
    import fails even though the package is installed. Fall back to loading
    straight from the repo's ``src/`` tree (setup.py maps src → langbridge_code).
    """
    try:
        from langbridge_code import settings as lb

        return lb
    except ModuleNotFoundError:
        pass
    src = Path(__file__).resolve().parents[3] / "src"
    if not (src / "settings.py").is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "langbridge_code",
        src / "__init__.py",
        submodule_search_locations=[str(src)],
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["langbridge_code"] = module
    spec.loader.exec_module(module)
    from langbridge_code import settings as lb

    return lb


def resolve_chat_route(
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> tuple[dict[str, str] | None, str | None]:
    """Resolve ``{api_key, base_url, model}`` for an OpenAI-compatible chat call.

    Returns ``(route, error)``. ``error`` is set when no usable key/base is found.
    """
    preferred_model = (model or os.environ.get("LB_INTERACTIVE_MODEL") or "").strip() or None
    preferred_base = (base_url or os.environ.get("OPENAI_BASE_URL") or "").strip() or None

    try:
        lb = _load_langbridge_settings()
        if lb is None:
            raise ModuleNotFoundError("langbridge_code")

        route = lb.resolve_llm_route(preferred_model)
        key = route.get("api_key")
        provider = route.get("provider") or lb.active_api_provider()
        if not key:
            # Active model/provider may lack a key; try any configured provider.
            for name in ("deepseek", "moonshot", "openai", "anthropic"):
                candidate = lb.resolve_provider_api_key(name)
                if candidate:
                    key = candidate
                    provider = name
                    break
        resolved_base = (
            preferred_base
            or (route.get("base_url") or "").strip()
            or lb.provider_base_url(provider)
            or ""
        ).rstrip("/")
        resolved_model = (
            preferred_model
            or (lb.DEFAULT_MODEL or "").strip()
            or None
        )
        if not resolved_model and provider:
            providers = (lb.load_config().get("api") or {}).get("providers") or {}
            resolved_model = ((providers.get(provider) or {}).get("model") or "").strip() or None
        if key and resolved_base and resolved_model:
            return (
                {
                    "api_key": key,
                    "base_url": resolved_base,
                    "model": resolved_model,
                    "provider": provider,
                },
                None,
            )
        if key and resolved_base:
            return (
                {
                    "api_key": key,
                    "base_url": resolved_base,
                    "model": resolved_model or "gpt-4o-mini",
                    "provider": provider,
                },
                None,
            )
    except Exception:  # noqa: BLE001 — config optional in bare test envs
        pass

    key = api_key()
    base = (preferred_base or "https://api.openai.com/v1").rstrip("/")
    mdl = preferred_model or "gpt-4o-mini"
    if key:
        return (
            {"api_key": key, "base_url": base, "model": mdl, "provider": "env"},
            None,
        )
    return (
        None,
        "no API key (LangBridge config api_keys.* or OPENAI_API_KEY / "
        "LB_OPENAI_API_KEY / ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / MOONSHOT_API_KEY)",
    )


def _post_chat(base: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST one chat completion. Raises ``LLMError`` instead of returning None."""
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
    except urllib.error.HTTPError as err:  # subclass of URLError; must come first
        try:
            detail = err.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise LLMError(
            f"HTTP {err.code} {detail}".strip(), fatal=err.code in _FATAL_STATUS
        ) from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise LLMError(f"network: {err}") from err
    except json.JSONDecodeError as err:
        raise LLMError(f"response body was not JSON: {err}") from err


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


def chat_json_ex(
    *,
    system: str,
    user: str,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
    retries: int = RETRIES,
) -> tuple[dict[str, Any] | None, str | None]:
    """Call chat completions expecting a JSON object in the assistant text.

    Returns ``(parsed, error)``; ``error`` is None on success and a short
    human-readable reason otherwise. Transient failures are retried with
    backoff; fatal ones (no key, bad key, unknown model) return immediately.
    """
    route, route_error = resolve_chat_route(model=model, base_url=base_url)
    if not route:
        return None, route_error or "no API key"
    key = route["api_key"]
    base = route["base_url"]
    model = route["model"]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    attempts = max(1, int(retries))
    last = "unknown error"
    attempt = 0
    while attempt < attempts:
        try:
            body = _post_chat(base, key, payload)
        except LLMError as err:
            last = str(err)
            if err.fatal:
                # Some providers reject optional params (Anthropic: json_object
                # response_format; some OpenAI models: non-default temperature).
                # If the error names one we sent, strip it and retry for free.
                offending = [
                    p
                    for p in ("response_format", "temperature")
                    if p in payload and p in last
                ]
                if offending:
                    for p in offending:
                        payload.pop(p, None)
                    continue
                break
        else:
            try:
                text = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                last = f"unexpected response shape: {str(body)[:200]}"
            else:
                parsed = _parse_json_content(text)
                if parsed is not None:
                    return parsed, None
                last = f"assistant text was not a JSON object: {text[:200]}"
        attempt += 1
        if attempt < attempts:
            time.sleep(BACKOFF_SEC * (2 ** (attempt - 1)))
    return None, f"{model} @ {base}: {last}"


def chat_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
) -> dict[str, Any] | None:
    """``chat_json_ex`` without the reason; None means the call failed."""
    parsed, _ = chat_json_ex(
        system=system,
        user=user,
        model=model,
        base_url=base_url,
        temperature=temperature,
    )
    return parsed
