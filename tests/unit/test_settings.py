import json
from pathlib import Path

import langbridge_code.settings as settings


def test_load_api_key_uses_provider_specific_key(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text(
        json.dumps({
            "api_keys": {
                "moonshot": "sk-moon",
                "openai": "sk-openai",
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LANGBRIDGE_API_PROVIDER", raising=False)

    assert settings.load_api_key("moonshot") == "sk-moon"
    assert settings.load_api_key("openai") == "sk-openai"


def test_load_api_key_prefers_env_for_matching_provider(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)

    assert settings.load_api_key("openai") == "sk-env-openai"


def test_legacy_api_key_falls_back_to_active_provider(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text(json.dumps({"api_key": "sk-legacy"}), encoding="utf-8")
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.setenv("LANGBRIDGE_API_PROVIDER", "moonshot")
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    assert settings.load_api_key("moonshot") == "sk-legacy"
