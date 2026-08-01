"""interactive-bench/config.json is the source of truth for this bench's models."""
from __future__ import annotations

import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "data-pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib.bench_config import (  # noqa: E402
    agent_defaults,
    eval_run_metadata,
    interactive_defaults,
    load_bench_config,
    merge_agent_user_config,
    pipeline_defaults,
    resolve_coverage_model,
    resolve_intent_model,
    resolve_sim_model,
)


def test_shipped_config_defaults():
    cfg = load_bench_config()
    agent = agent_defaults(cfg)
    assert agent["provider"] == "deepseek"
    assert agent["model"] == "deepseek-v4-pro"
    assert agent["agent_models"]["explorer"] == "deepseek-v4-flash"
    interactive = interactive_defaults(cfg)
    assert interactive["sim_model"] == "gpt-5.6"
    assert interactive["coverage_model"] == "claude-fable-5"
    assert pipeline_defaults(cfg)["intent_model"] == "claude-fable-5"


def test_resolve_models_prefer_env(monkeypatch):
    monkeypatch.setenv("LB_SIM_MODEL", "sim-override")
    monkeypatch.setenv("LB_COVERAGE_MODEL", "cov-override")
    monkeypatch.setenv("LB_INTENT_MODEL", "intent-override")
    assert resolve_sim_model() == "sim-override"
    assert resolve_coverage_model() == "cov-override"
    assert resolve_intent_model() == "intent-override"


def test_generic_interactive_env_fallback(monkeypatch):
    monkeypatch.delenv("LB_SIM_MODEL", raising=False)
    monkeypatch.delenv("LB_COVERAGE_MODEL", raising=False)
    monkeypatch.delenv("LB_INTENT_MODEL", raising=False)
    monkeypatch.setenv("LB_INTERACTIVE_MODEL", "generic-override")
    assert resolve_sim_model() == "generic-override"
    assert resolve_coverage_model() == "generic-override"
    assert resolve_intent_model() == "generic-override"


def test_merge_agent_user_config_pins_bench_stack():
    merged = merge_agent_user_config({
        "model": "kimi-k3",
        "api": {"provider": "moonshot"},
        "api_keys": {"moonshot": "sk-moon", "deepseek": "sk-deep"},
    })
    assert merged["model"] is None
    assert merged["api"]["provider"] == "deepseek"
    assert merged["api"]["providers"]["deepseek"]["model"] == "deepseek-v4-pro"
    assert merged["api_keys"]["deepseek"] == "sk-deep"


def test_eval_run_metadata_shape(monkeypatch):
    monkeypatch.delenv("LB_SIM_MODEL", raising=False)
    monkeypatch.delenv("LB_COVERAGE_MODEL", raising=False)
    monkeypatch.delenv("LB_INTERACTIVE_MODEL", raising=False)
    meta = eval_run_metadata(agent_model="custom-agent", stub=True)
    assert meta["agent"]["model"] == "custom-agent"
    assert meta["agent"]["cli_model_override"] is True
    assert meta["interactive"]["sim_model"] == "gpt-5.6"
    assert meta["interactive"]["coverage_model"] == "claude-fable-5"
    assert meta["stub"] is True
    assert meta["config_path"].endswith("interactive-bench/config.json")
