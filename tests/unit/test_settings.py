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


def test_load_api_key_uses_deepseek_env(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-deepseek")

    assert settings.load_api_key("deepseek") == "sk-env-deepseek"


def test_sanitize_api_key_strips_terminal_escape_junk():
    junk = "\x1b[<0;21;23M\x1b[<0;21;23Msk-real-key"
    assert settings.sanitize_api_key(junk) == "sk-real-key"
    assert settings.sanitize_api_key("  sk-clean  ") == "sk-clean"
    assert settings.sanitize_api_key("\x1b[<0;1;2M") is None
    assert settings.sanitize_api_key("") is None
    assert settings.sanitize_api_key(None) is None


def test_load_api_key_strips_escape_junk_from_config(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text(
        json.dumps({
            "api_keys": {
                "moonshot": "\x1b[<0;21;23Msk-clean-moon",
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    assert settings.load_api_key("moonshot") == "sk-clean-moon"


def test_save_api_key_persists_sanitized_value(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)

    settings.save_api_key("\x1b[<0;21;23Msk-saved", "moonshot")
    saved = json.loads(user_cfg.read_text(encoding="utf-8"))
    assert saved["api_keys"]["moonshot"] == "sk-saved"


def test_prompt_and_save_api_key_retries_until_valid(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.setattr(settings.sys.stdin, "isatty", lambda: True)
    answers = iter(["bad-key", "sk-good"])
    monkeypatch.setattr(settings.getpass, "getpass", lambda _prompt: next(answers))
    attempts = {"n": 0}

    def fake_validate(api_key, *, provider=None):
        attempts["n"] += 1
        if api_key == "sk-good":
            return True, "ok"
        return False, "401 unauthorized"

    monkeypatch.setattr(settings, "validate_api_key", fake_validate)

    assert settings._prompt_and_save_api_key("moonshot") == "sk-good"
    assert attempts["n"] == 2
    saved = json.loads(user_cfg.read_text(encoding="utf-8"))
    assert saved["api_keys"]["moonshot"] == "sk-good"


def test_prompt_and_save_api_key_non_interactive_errors(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.setattr(settings.sys.stdin, "isatty", lambda: False)

    try:
        settings._prompt_and_save_api_key("moonshot")
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "No Moonshot/Kimi API key" in str(error)


def test_provider_binding_resolves_deepseek_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("LANGBRIDGE_API_PROVIDER", "deepseek")
    monkeypatch.delenv("LANGBRIDGE_MODEL", raising=False)
    monkeypatch.delenv("LANGBRIDGE_API_BASE_URL", raising=False)
    try:
        settings._bind(settings.load_config())
        assert settings.API_PROVIDER == "deepseek"
        assert settings.DEFAULT_MODEL == "deepseek-v4-pro"
        assert settings.API_BASE_URL == "https://api.deepseek.com"
        # Per-agent overrides: explorer runs on the cheaper flash model.
        assert settings.model_for_agent("explorer") == "deepseek-v4-flash"
        assert settings.model_for_agent("worker") == "deepseek-v4-pro"
        assert settings.model_for_agent("worker", "custom-model") == "custom-model"
    finally:
        monkeypatch.undo()
        settings._bind(settings.load_config())


def test_choose_api_provider_uses_saved_choice(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text(json.dumps({"api": {"provider": "deepseek"}}), encoding="utf-8")
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.delenv("LANGBRIDGE_API_PROVIDER", raising=False)

    assert settings.choose_api_provider() == "deepseek"


def test_choose_api_provider_non_interactive_falls_back(monkeypatch, tmp_path):
    user_cfg = tmp_path / "missing.json"
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.delenv("LANGBRIDGE_API_PROVIDER", raising=False)
    monkeypatch.setattr(settings.sys.stdin, "isatty", lambda: False)

    assert settings.choose_api_provider() == settings.active_api_provider()


def test_artifacts_dir_defaults_under_install_root_per_project():
    expected = settings.INSTALL_ROOT / "artifacts" / settings.WORKSPACE_ROOT.name
    assert settings.ARTIFACTS_DIR == expected


def test_set_default_model_persists(monkeypatch, tmp_path):
    user_cfg = tmp_path / "config.json"
    user_cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "USER_CONFIG_PATH", user_cfg)
    monkeypatch.delenv("LANGBRIDGE_MODEL", raising=False)
    try:
        assert settings.set_default_model("picked-model") == "picked-model"
        saved = json.loads(user_cfg.read_text(encoding="utf-8"))
        assert saved["model"] == "picked-model"
        assert settings.DEFAULT_MODEL == "picked-model"
    finally:
        monkeypatch.undo()
        settings._bind(settings.load_config())


def test_list_model_catalog_uses_config_only(monkeypatch):
    """Picker must not dump live /models — only curated config entries."""
    cfg = {
        "api": {
            "provider": "moonshot",
            "providers": {
                "moonshot": {
                    "model": "kimi-k2.7-code",
                    "models": ["kimi-k3"],
                },
                "deepseek": {"model": "deepseek-v4-pro"},
            },
        }
    }
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "active_api_provider", lambda: "moonshot")
    catalog = settings.list_model_catalog("sk-test")
    assert [e["id"] for e in catalog] == [
        "kimi-k2.7-code",
        "kimi-k3",
        "deepseek-v4-pro",
    ]
    assert settings.list_available_models("sk-test") == [
        "kimi-k2.7-code",
        "kimi-k3",
        "deepseek-v4-pro",
    ]


def test_infer_provider_for_kimi_k3():
    assert settings.infer_provider_for_model("kimi-k3") == "moonshot"
    assert settings.infer_provider_for_model("deepseek-v4-pro") == "deepseek"
