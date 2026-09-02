"""Resolve each model's native context window size."""
from __future__ import annotations

# Built-in defaults; config.json context.model_context_windows overrides/extends these.
_BUILTIN_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "kimi-k3": 1_048_576,
    "k3": 1_048_576,
    "kimi-k2.7-code": 262_144,
    "kimi-k2.7": 262_144,
    "kimi-k2.5": 262_144,
    "kimi-k2": 262_144,
    "kimi": 131_072,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
    "gpt-5.6": 1_050_000,
    "gpt-5.6-sol": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    "gpt-5.5": 1_050_000,
    "gpt-5.5-pro": 1_050_000,
    "gpt-5.4": 1_050_000,
    "gpt-5.3-codex": 400_000,
    "gpt-5.1": 400_000,
    "gpt-5": 400_000,
    "gpt-4.1": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
}

_PREFIX_WINDOWS: list[tuple[str, int]] = [
    ("kimi-k3", 1_048_576),
    ("k3", 1_048_576),
    ("kimi-k2.7", 262_144),
    ("kimi-k2", 262_144),
    ("kimi", 131_072),
    ("deepseek-v4", 1_000_000),
    ("deepseek", 128_000),
    ("gpt-5.6", 1_050_000),
    ("gpt-5.5", 1_050_000),
    ("gpt-5.4", 1_050_000),
    ("gpt-5.1", 400_000),
    ("gpt-5", 400_000),
    ("gpt-4.1", 1_047_576),
    ("gpt-4o", 128_000),
    ("gpt-4", 128_000),
    ("o4-mini", 200_000),
    ("o3-mini", 200_000),
    ("o3", 200_000),
]


def _normalize_model(model: str) -> str:
    return (model or "").strip().lower()


def _registry() -> dict[str, int]:
    from langbridge_code.settings import MODEL_CONTEXT_WINDOWS

    merged = dict(_BUILTIN_MODEL_CONTEXT_WINDOWS)
    for name, window in (MODEL_CONTEXT_WINDOWS or {}).items():
        if window:
            merged[_normalize_model(name)] = int(window)
    return merged


def model_context_window(model: str) -> int | None:
    """Return the model provider's full context window in tokens, or None if unknown."""
    name = _normalize_model(model)
    registry = _registry()
    if name in registry:
        return registry[name]

    for prefix, window in _PREFIX_WINDOWS:
        if name.startswith(prefix):
            return window

    # Provider paths like moonshot/kimi-k2.7-code
    if "/" in name:
        return model_context_window(name.rsplit("/", 1)[-1])

    return None


def format_token_count(value: int | None) -> str:
    if value is None:
        return ""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)
