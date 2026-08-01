"""Interactive-bench model config (``interactive-bench/config.json``).

Source of truth for every model this bench uses; separate from
``eval/config.json`` (langbridge-bench). Sections:

- ``agent``: SUT stack (provider / model / per-role agent_models) for the
  agent under test.
- ``interactive``: sim + coverage-judge models for the eval harness.
  Env overrides: ``LB_SIM_MODEL`` / ``LB_COVERAGE_MODEL`` / ``LB_INTERACTIVE_MODEL``.
- ``pipeline``: intent-extraction model for the data pipeline.
  Env overrides: ``LB_INTENT_MODEL`` / ``LB_INTERACTIVE_MODEL``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BENCH_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"

_DEFAULT_AGENT = {
    "provider": "deepseek",
    "model": "deepseek-v4-pro",
    "agent_models": {"explorer": "deepseek-v4-flash"},
}

_DEFAULT_INTERACTIVE = {
    "sim_model": "gpt-5.6",
    "coverage_model": "claude-fable-5",
}

_DEFAULT_PIPELINE = {
    "intent_model": "claude-fable-5",
}


def load_bench_config(path: Path | None = None) -> dict:
    cfg_path = path or BENCH_CONFIG_PATH
    if not cfg_path.is_file():
        return {}
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def agent_defaults(cfg: dict | None = None) -> dict:
    """Normalized SUT agent defaults: provider, model, agent_models."""
    agent = {
        "provider": _DEFAULT_AGENT["provider"],
        "model": _DEFAULT_AGENT["model"],
        "agent_models": dict(_DEFAULT_AGENT["agent_models"]),
    }
    raw = (cfg if cfg is not None else load_bench_config()).get("agent") or {}
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
    """Fill ``LANGBRIDGE_API_PROVIDER`` from bench config when unset.

    Does not set ``LANGBRIDGE_MODEL`` by default: that env var overrides every
    role (including explorer flash). Pin the stack via ``agent_config_overlay``
    instead. CLI ``--model`` may still set ``LANGBRIDGE_MODEL``.
    """
    out = dict(env or {})
    agent = agent_defaults(cfg)
    out.setdefault("LANGBRIDGE_API_PROVIDER", agent["provider"])
    return out


def agent_config_overlay(cfg: dict | None = None) -> dict:
    """User-config patch so container settings match this bench's SUT stack."""
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
    """Merge this bench's SUT overlay onto a host user config (overlay wins)."""
    merged = _deep_merge(dict(user_cfg or {}), agent_config_overlay(cfg))
    merged["model"] = None
    return merged


def interactive_defaults(cfg: dict | None = None) -> dict:
    """Normalized harness model defaults: sim + coverage judge."""
    out = dict(_DEFAULT_INTERACTIVE)
    raw = (cfg if cfg is not None else load_bench_config()).get("interactive") or {}
    if isinstance(raw, dict):
        for key in ("sim_model", "coverage_model"):
            value = str(raw.get(key) or "").strip()
            if value:
                out[key] = value
    return out


def pipeline_defaults(cfg: dict | None = None) -> dict:
    """Normalized data-pipeline model defaults: intent extraction."""
    out = dict(_DEFAULT_PIPELINE)
    raw = (cfg if cfg is not None else load_bench_config()).get("pipeline") or {}
    if isinstance(raw, dict):
        value = str(raw.get("intent_model") or "").strip()
        if value:
            out["intent_model"] = value
    return out


def resolve_sim_model(*, cfg: dict | None = None) -> str:
    """User-sim model: env override, else ``interactive.sim_model``."""
    return (
        (os.environ.get("LB_SIM_MODEL") or "").strip()
        or (os.environ.get("LB_INTERACTIVE_MODEL") or "").strip()
        or interactive_defaults(cfg)["sim_model"]
    )


def resolve_coverage_model(*, cfg: dict | None = None) -> str:
    """Intent-coverage judge model: env, else ``interactive.coverage_model``."""
    return (
        (os.environ.get("LB_COVERAGE_MODEL") or "").strip()
        or (os.environ.get("LB_INTERACTIVE_MODEL") or "").strip()
        or interactive_defaults(cfg)["coverage_model"]
    )


def resolve_intent_model(*, cfg: dict | None = None) -> str:
    """Intent-extraction model: env, else ``pipeline.intent_model``."""
    return (
        (os.environ.get("LB_INTENT_MODEL") or "").strip()
        or (os.environ.get("LB_INTERACTIVE_MODEL") or "").strip()
        or pipeline_defaults(cfg)["intent_model"]
    )


def eval_run_metadata(
    *,
    agent_model: str | None = None,
    stub: bool = False,
    cfg: dict | None = None,
) -> dict:
    """Snapshot of models used for one interactive eval run (into report.json)."""
    config = cfg if cfg is not None else load_bench_config()
    agent = agent_defaults(config)
    sut_model = (agent_model or "").strip() or agent["model"]
    return {
        "agent": {
            "provider": agent["provider"],
            "model": sut_model,
            "agent_models": dict(agent.get("agent_models") or {}),
            "cli_model_override": bool((agent_model or "").strip()),
        },
        "interactive": {
            "sim_model": resolve_sim_model(cfg=config),
            "coverage_model": resolve_coverage_model(cfg=config),
        },
        "stub": bool(stub),
        "config_path": str(BENCH_CONFIG_PATH),
    }
