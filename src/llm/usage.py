"""Normalize provider usage fields and track session cache-hit totals."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from langbridge_code.util.artifacts import artifact_dir
from langbridge_code.util.trace_log import get_trace_context, write_line

LLM_USAGE_JSON = "llm_usage.json"

_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCK_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _as_dict(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(exclude_none=True)
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            pass
    out = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "input_tokens_details",
        "prompt_tokens_details",
        "completion_tokens_details",
    ):
        if hasattr(value, key):
            out[key] = getattr(value, key)
    return out


def _nested_cached(details) -> int:
    details = _as_dict(details)
    for key in ("cached_tokens", "cache_read_tokens", "cached"):
        raw = details.get(key)
        if raw is not None:
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                return 0
    return 0


def _cache_was_reported(usage: dict) -> bool:
    if any(
        key in usage
        for key in (
            "cache_read_input_tokens",
            "prompt_cache_hit_tokens",
            "cached_tokens",
        )
    ):
        return True
    for details_key in ("input_tokens_details", "prompt_tokens_details"):
        details = _as_dict(usage.get(details_key))
        if any(key in details for key in ("cached_tokens", "cache_read_tokens", "cached")):
            return True
    return False


def normalize_usage(raw) -> dict | None:
    """Return a flat usage dict, or None when the provider sent nothing useful.

    Keys: input_tokens, output_tokens, cached_tokens, cache_write_tokens,
    cache_reported.
    """
    usage = _as_dict(raw)
    if not usage:
        return None

    try:
        input_tokens = int(
            usage.get("input_tokens")
            if usage.get("input_tokens") is not None
            else usage.get("prompt_tokens") or 0
        )
        output_tokens = int(
            usage.get("output_tokens")
            if usage.get("output_tokens") is not None
            else usage.get("completion_tokens") or 0
        )
    except (TypeError, ValueError):
        return None

    cached = 0
    for key in (
        "cache_read_input_tokens",
        "prompt_cache_hit_tokens",
        "cached_tokens",
    ):
        raw_cached = usage.get(key)
        if raw_cached is not None:
            try:
                cached = max(cached, int(raw_cached))
            except (TypeError, ValueError):
                pass
    cached = max(
        cached,
        _nested_cached(usage.get("input_tokens_details")),
        _nested_cached(usage.get("prompt_tokens_details")),
    )

    cache_write = 0
    for key in ("cache_creation_input_tokens", "prompt_cache_miss_tokens"):
        # DeepSeek's miss tokens are not a write premium; skip as write.
        if key == "prompt_cache_miss_tokens":
            continue
        raw_write = usage.get(key)
        if raw_write is not None:
            try:
                cache_write = max(cache_write, int(raw_write))
            except (TypeError, ValueError):
                pass

    if input_tokens <= 0 and output_tokens <= 0 and cached <= 0 and cache_write <= 0:
        return None
    return {
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "cached_tokens": max(0, min(cached, max(0, input_tokens) or cached)),
        "cache_write_tokens": max(0, cache_write),
        "cache_reported": _cache_was_reported(usage),
    }


def cache_hit_rate(input_tokens: int, cached_tokens: int) -> float | None:
    if input_tokens <= 0:
        return None
    return max(0.0, min(1.0, cached_tokens / input_tokens))


def format_usage_line(usage: dict, *, session: dict | None = None) -> str:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cached = int(usage.get("cached_tokens") or 0)
    hit = (
        cache_hit_rate(input_tokens, cached)
        if usage.get("cache_reported", True)
        else None
    )
    hit_text = f"{hit * 100:.1f}%" if hit is not None else "n/a"
    parts = [
        f"usage: in={input_tokens}",
        f"cached={cached}",
        f"out={output_tokens}",
        f"hit={hit_text}",
    ]
    write = int(usage.get("cache_write_tokens") or 0)
    if write:
        parts.append(f"cache_write={write}")
    if session:
        s_in = int(session.get("input_tokens") or 0)
        s_cached = int(session.get("cached_tokens") or 0)
        s_hit = cache_hit_rate(s_in, s_cached)
        s_text = f"{s_hit * 100:.1f}%" if s_hit is not None else "n/a"
        parts.append(f"session_hit={s_text} ({s_cached}/{s_in})")
        parts.append(f"calls={int(session.get('calls') or 0)}")
    return " ".join(parts)


def _empty_totals() -> dict:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "calls_with_usage": 0,
        "calls_with_cache_hit": 0,
        "calls_with_cache_data": 0,
    }


def _usage_path(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / LLM_USAGE_JSON


def record_usage(usage: dict | None, *, label: str = "agent") -> dict | None:
    """Persist one call into session llm_usage.json and log a session.md line."""
    if not usage:
        return None
    ctx = get_trace_context()
    run = ctx.run_log_path if ctx else None
    path = _usage_path(run)
    totals = None
    if path is not None:
        lock = _lock_for(path)
        with lock:
            totals = _empty_totals()
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        totals.update({k: loaded.get(k, totals[k]) for k in totals})
                except (OSError, json.JSONDecodeError):
                    pass
            totals["calls"] = int(totals.get("calls") or 0) + 1
            totals["calls_with_usage"] = int(totals.get("calls_with_usage") or 0) + 1
            for key in (
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "cache_write_tokens",
            ):
                totals[key] = int(totals.get(key) or 0) + int(usage.get(key) or 0)
            if int(usage.get("cached_tokens") or 0) > 0:
                totals["calls_with_cache_hit"] = (
                    int(totals.get("calls_with_cache_hit") or 0) + 1
                )
            if usage.get("cache_reported", True):
                totals["calls_with_cache_data"] = (
                    int(totals.get("calls_with_cache_data") or 0) + 1
                )
            hit = (
                cache_hit_rate(
                    int(totals["input_tokens"]), int(totals["cached_tokens"])
                )
                if int(totals.get("calls_with_cache_data") or 0) > 0
                else None
            )
            totals["cache_hit_rate"] = round(hit, 6) if hit is not None else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(totals, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    write_line(label, format_usage_line(usage, session=totals))
    return totals


def load_usage_totals(run_log_path) -> dict | None:
    """Read the current task's cumulative usage, if any has been recorded."""
    path = _usage_path(run_log_path)
    if path is None or not path.is_file():
        return None
    lock = _lock_for(path)
    with lock:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "LLM_USAGE_JSON",
    "cache_hit_rate",
    "format_usage_line",
    "load_usage_totals",
    "normalize_usage",
    "record_usage",
]
