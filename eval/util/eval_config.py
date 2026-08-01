"""Eval defaults for the agent under test (SUT).

Interactive CLI may use moonshot/openai/etc.; evals default the SUT to the
DeepSeek stack in ``eval/config.json`` unless env / CLI already chose a
provider or model.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

EVAL_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"

_DEFAULT_AGENT = {
    "provider": "deepseek",
    "model": "deepseek-v4-pro",
    "agent_models": {"explorer": "deepseek-v4-flash"},
}

# langbridge-bench dataset-pipeline LLM role: curate (judge + classify).
# Env override: LB_CURATE_MODEL. Interactive-bench models live in
# ``interactive-bench/config.json`` (see _lib/bench_config.py there).
_DEFAULT_PIPELINE = {
    "curate_model": "claude-fable-5",
}


def load_eval_config(path: Path | None = None) -> dict:
    cfg_path = path or EVAL_CONFIG_PATH
    if not cfg_path.is_file():
        return {"agent": dict(_DEFAULT_AGENT)}
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"agent": dict(_DEFAULT_AGENT)}
    return data


def agent_defaults(cfg: dict | None = None) -> dict:
    """Normalized SUT agent defaults: provider, model, agent_models."""
    agent = {
        "provider": _DEFAULT_AGENT["provider"],
        "model": _DEFAULT_AGENT["model"],
        "agent_models": dict(_DEFAULT_AGENT["agent_models"]),
    }
    raw = (cfg if cfg is not None else load_eval_config()).get("agent") or {}
    if isinstance(raw, dict):
        if raw.get("provider"):
            agent["provider"] = str(raw["provider"]).strip()
        if raw.get("model"):
            agent["model"] = str(raw["model"]).strip()
        models = raw.get("agent_models")
        if isinstance(models, dict) and models:
            agent["agent_models"] = {
                str(role): str(model).strip()
                for role, model in models.items()
                if str(model or "").strip()
            }
    return agent


def apply_agent_env(env: dict | None = None, *, cfg: dict | None = None) -> dict:
    """Fill ``LANGBRIDGE_API_PROVIDER`` from eval config when unset.

    Does not set ``LANGBRIDGE_MODEL`` by default: that env var overrides every
    role (including explorer flash). Pin the stack via ``agent_config_overlay``
    / packaged provider ``agent_models`` instead. CLI ``--model`` may still set
    ``LANGBRIDGE_MODEL`` for a single-model run.
    """
    out = dict(env or {})
    agent = agent_defaults(cfg)
    out.setdefault("LANGBRIDGE_API_PROVIDER", agent["provider"])
    return out


def agent_config_overlay(cfg: dict | None = None) -> dict:
    """User-config patch so container settings match the DeepSeek SUT stack."""
    agent = agent_defaults(cfg)
    return {
        "model": None,
        "api": {
            "provider": agent["provider"],
            "providers": {
                agent["provider"]: {
                    "model": agent["model"],
                    "agent_models": dict(agent.get("agent_models") or {}),
                }
            },
        },
    }


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_agent_user_config(user_cfg: dict | None, *, cfg: dict | None = None) -> dict:
    """Merge eval SUT overlay onto a host user config (overlay wins)."""
    merged = _deep_merge(dict(user_cfg or {}), agent_config_overlay(cfg))
    merged["model"] = None
    return merged


def default_eval_model() -> str:
    """Explicit single-model override from env, else empty (use provider stack)."""
    return (os.environ.get("LANGBRIDGE_MODEL") or "").strip()


def pipeline_defaults(cfg: dict | None = None) -> dict:
    """Normalized dataset-pipeline model defaults: curate."""
    out = dict(_DEFAULT_PIPELINE)
    raw = (cfg if cfg is not None else load_eval_config()).get("pipeline") or {}
    if isinstance(raw, dict):
        value = str(raw.get("curate_model") or "").strip()
        if value:
            out["curate_model"] = value
    return out


def resolve_curate_model(*, cfg: dict | None = None) -> str:
    """langbridge-bench curate judge/classify model: env, else ``pipeline.curate_model``."""
    return (
        (os.environ.get("LB_CURATE_MODEL") or "").strip()
        or pipeline_defaults(cfg)["curate_model"]
    )


def eval_run_metadata(
    *,
    agent_model: str | None = None,
    stub: bool = False,
    cfg: dict | None = None,
) -> dict:
    """Snapshot of SUT models for one langbridge-bench eval run (report.json).

    Interactive-bench has its own version in
    ``interactive-bench/data-pipeline/_lib/bench_config.py``.
    """
    config = cfg if cfg is not None else load_eval_config()
    agent = agent_defaults(config)
    sut_model = (agent_model or "").strip() or agent["model"]
    return {
        "agent": {
            "provider": agent["provider"],
            "model": sut_model,
            "agent_models": dict(agent.get("agent_models") or {}),
            "cli_model_override": bool((agent_model or "").strip()),
        },
        "stub": bool(stub),
        "config_path": str(EVAL_CONFIG_PATH),
    }
