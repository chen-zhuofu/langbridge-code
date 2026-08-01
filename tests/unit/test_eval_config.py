import json
from pathlib import Path

from util.eval_config import (
    agent_config_overlay,
    agent_defaults,
    apply_agent_env,
    eval_run_metadata,
    load_eval_config,
    merge_agent_user_config,
    pipeline_defaults,
    resolve_curate_model,
)


def test_load_eval_config_defaults_to_deepseek_stack():
    cfg = load_eval_config()
    agent = agent_defaults(cfg)
    assert agent["provider"] == "deepseek"
    assert agent["model"] == "deepseek-v4-pro"
    assert agent["agent_models"]["explorer"] == "deepseek-v4-flash"


def test_apply_agent_env_sets_provider_without_forcing_model(monkeypatch):
    monkeypatch.delenv("LANGBRIDGE_API_PROVIDER", raising=False)
    monkeypatch.delenv("LANGBRIDGE_MODEL", raising=False)
    env = apply_agent_env({})
    assert env["LANGBRIDGE_API_PROVIDER"] == "deepseek"
    assert "LANGBRIDGE_MODEL" not in env


def test_apply_agent_env_respects_existing_provider(monkeypatch):
    env = apply_agent_env({"LANGBRIDGE_API_PROVIDER": "openai"})
    assert env["LANGBRIDGE_API_PROVIDER"] == "openai"


def test_merge_agent_user_config_clears_cli_model_and_pins_deepseek():
    merged = merge_agent_user_config({
        "model": "kimi-k3",
        "api": {"provider": "moonshot"},
        "api_keys": {"moonshot": "sk-moon", "deepseek": "sk-deep"},
    })
    assert merged["model"] is None
    assert merged["api"]["provider"] == "deepseek"
    assert merged["api"]["providers"]["deepseek"]["model"] == "deepseek-v4-pro"
    assert merged["api"]["providers"]["deepseek"]["agent_models"]["explorer"] == (
        "deepseek-v4-flash"
    )
    assert merged["api_keys"]["deepseek"] == "sk-deep"


def test_agent_config_overlay_shape():
    overlay = agent_config_overlay()
    assert overlay["model"] is None
    assert overlay["api"]["provider"] == "deepseek"


def test_custom_eval_config_path(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({
            "agent": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "agent_models": {"explorer": "deepseek-v4-flash"},
            },
            "pipeline": {"curate_model": "curate-test"},
        }),
        encoding="utf-8",
    )
    agent = agent_defaults(load_eval_config(path))
    assert agent["model"] == "deepseek-v4-flash"
    pipeline = pipeline_defaults(load_eval_config(path))
    assert pipeline["curate_model"] == "curate-test"


def test_pipeline_defaults_from_shipped_config():
    pipeline = pipeline_defaults(load_eval_config())
    assert pipeline["curate_model"] == "claude-fable-5"


def test_resolve_curate_model_prefers_env(monkeypatch):
    monkeypatch.setenv("LB_CURATE_MODEL", "curate-override")
    assert resolve_curate_model() == "curate-override"
    monkeypatch.delenv("LB_CURATE_MODEL", raising=False)
    assert resolve_curate_model() == "claude-fable-5"


def test_eval_run_metadata_shape():
    meta = eval_run_metadata(agent_model="custom-agent", stub=False)
    assert meta["agent"]["model"] == "custom-agent"
    assert meta["agent"]["cli_model_override"] is True
    assert "interactive" not in meta
    assert meta["stub"] is False
    assert meta["config_path"].endswith("eval/config.json")
