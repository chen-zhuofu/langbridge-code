"""Load LangBridge settings from config.json.

Defaults ship with the package (langbridge_code/config.json).
Per-user overrides live at ~/.langbridge-code/config.json.
Environment variables still override secrets, model, and runtime paths.
"""
import getpass
import json
import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "config.json"
# langbridge-code checkout root (src/langbridge_code -> langbridge-code).
INSTALL_ROOT = PACKAGE_DIR.parents[1]

CONFIG_DIR = Path.home() / ".langbridge-code"
USER_CONFIG_PATH = CONFIG_DIR / "config.json"

def _deep_merge(base, override):
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config():
    data = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    if USER_CONFIG_PATH.exists():
        data = _deep_merge(data, json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8")))
    return data


def save_user_config(patch):
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if USER_CONFIG_PATH.exists():
        current = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
    USER_CONFIG_PATH.write_text(
        json.dumps(_deep_merge(current, patch), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    USER_CONFIG_PATH.chmod(0o600)


def _path_override(env_name, config_value, default):
    env = os.environ.get(env_name)
    if env:
        return Path(env)
    if config_value:
        return Path(config_value)
    return default


def _env_bool(env_name, default: bool) -> bool:
    raw = os.environ.get(env_name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bind(cfg):
    agent = cfg["agent"]
    goal = cfg.get("goal", {})
    context = cfg["context"]
    fs = cfg["tools"]["filesystem"]
    execution = cfg["tools"]["execution"]
    testing = cfg["tools"]["testing"]
    web = cfg["tools"]["web"]
    debug = cfg["tools"]["debug"]
    training = cfg["training"]
    paths = cfg.get("paths", {})
    api = cfg.get("api", {})

    provider = os.environ.get("LANGBRIDGE_API_PROVIDER", api.get("provider", "openai"))
    provider_cfg = (api.get("providers") or {}).get(provider, {})

    globals().update({
        "DEFAULT_MODEL": os.environ.get("LANGBRIDGE_MODEL")
            or cfg.get("model")
            or provider_cfg.get("model", ""),
        "API_PROVIDER": provider,
        "AGENT_MODELS": dict(provider_cfg.get("agent_models") or {}),
        "API_BASE_URL": os.environ.get("LANGBRIDGE_API_BASE_URL")
            or provider_cfg.get("base_url", ""),
        "API_TIMEOUT_SECONDS": float(
            os.environ.get("LANGBRIDGE_API_TIMEOUT_SECONDS", api.get("timeout_seconds", 120))
        ),
        "API_MAX_RETRIES": int(
            os.environ.get("LANGBRIDGE_API_MAX_RETRIES", api.get("max_retries", 2))
        ),
        "API_STREAMING_ENABLED": _env_bool(
            "LANGBRIDGE_API_STREAMING_ENABLED",
            api.get("streaming_enabled", True),
        ),
        "DEFAULT_MAX_GUIDANCE": cfg["max_guidance"],
        "MAX_AGENT_STEPS": agent["max_agent_steps"],
        "MAX_AGENT_SECONDS": agent.get("max_agent_seconds", 3600),
        "MAX_EXPLORER_STEPS": agent.get("max_explorer_steps", 30),
        "MAX_EXPLORER_SECONDS": agent.get("max_explorer_seconds", 900),
        "MAX_WORKER_STEPS": agent.get("max_worker_steps", 30),
        "MAX_WORKER_SECONDS": agent.get("max_worker_seconds", 900),
        "MAX_REVIEWER_STEPS": agent.get("max_reviewer_steps", 30),
        "MAX_REVIEWER_SECONDS": agent.get("max_reviewer_seconds", 900),
        "MAX_WORKFLOW_SECONDS": agent.get("max_workflow_seconds", 3600),
        "MAX_PLANNER_STEPS": agent.get("max_planner_steps", 30),
        "MAX_PLANNER_SECONDS": agent.get("max_planner_seconds", 600),
        "MAX_WORKER_REVIEWER_STEPS": agent.get("max_worker_reviewer_steps", agent.get("max_agent_steps", 50)),
        "MAX_WORKER_REVIEWER_SECONDS": agent.get("max_worker_reviewer_seconds", 1800),
        "WORKFLOW_OUTER_MULTIPLIER": agent.get("workflow_outer_multiplier", 2),
        "MAX_PARALLEL_TOOL_CALLS": int(
            os.environ.get("LANGBRIDGE_MAX_PARALLEL_TOOL_CALLS", agent.get("max_parallel_tool_calls", 4))
        ),
        "MAX_PARALLEL_WORKERS": int(
            os.environ.get(
                "LANGBRIDGE_MAX_PARALLEL_WORKERS",
                agent.get("max_parallel_workers", 2),
            )
        ),
        "PARALLEL_AGENTS_ENABLED": _env_bool(
            "LANGBRIDGE_PARALLEL_AGENTS_ENABLED",
            agent.get("parallel_agents_enabled", True),
        ),
        "GOAL_DEFAULT_MAX_TURNS": int(
            os.environ.get("LANGBRIDGE_GOAL_DEFAULT_MAX_TURNS", goal.get("default_max_turns", 20))
        ),
        "GOAL_EVAL_INPUT_CHARS": int(
            os.environ.get("LANGBRIDGE_GOAL_EVAL_INPUT_CHARS", goal.get("eval_input_chars", 12000))
        ),
        "GOAL_EVALUATOR_MAX_STEPS": int(
            os.environ.get(
                "LANGBRIDGE_GOAL_EVALUATOR_MAX_STEPS",
                goal.get("evaluator_max_steps", 5),
            )
        ),
        "CONTEXT_WINDOW_MAX_FRACTION": float(
            os.environ.get(
                "LANGBRIDGE_CONTEXT_WINDOW_MAX_FRACTION",
                context.get("context_window_max_fraction", 0.4),
            )
        ),
        "DEFAULT_CONTEXT_WINDOW_TOKENS": int(
            context.get("default_context_window_tokens", 128000)
        ),
        "MODEL_CONTEXT_WINDOWS": context.get("model_context_windows", {}),
        "MAX_SESSION_CHOICES": context["max_session_choices"],
        "MAX_SESSION_SUMMARY_INPUT_CHARS": context["max_session_summary_input_chars"],
        # Raw tail kept on compaction: one more than the progress-note cadence
        # (11 > 10) so the compressed middle always overlaps progress.md.
        "COMPACT_RAW_KEEP": int(context.get("compact_raw_keep", 11)),
        "COMPACT_FRACTION": float(context.get("compact_fraction", 0.4)),
        "PROGRESS_MAX_FRACTION": float(context.get("progress_max_fraction", 0.1)),
        "TRACES_RESUME_MAX_FRACTION": float(context.get("traces_resume_max_fraction", 0.3)),
        "COMPACT_USE_LLM": context.get("compact_use_llm", True),
        "COMPACT_PROSE_TARGET_CHARS": int(context.get("compact_prose_target_chars", 16000)),
        "PROGRESS_NOTE_REMINDER_ROUNDS": int(context.get("progress_note_reminder_rounds", 10)),
        "MAX_FILE_BYTES": fs["max_file_bytes"],
        "MAX_SEARCH_FILE_BYTES": fs["max_search_file_bytes"],
        "MAX_EXECUTION_OUTPUT_CHARS": execution["max_output_chars"],
        "DEFAULT_EXECUTION_TIMEOUT_SECONDS": execution["default_timeout_seconds"],
        "MAX_EXECUTION_TIMEOUT_SECONDS": execution["max_timeout_seconds"],
        "MAX_TEST_OUTPUT_CHARS": testing["max_output_chars"],
        "DEFAULT_TEST_TIMEOUT_SECONDS": testing["default_timeout_seconds"],
        "MAX_TEST_TIMEOUT_SECONDS": testing["max_timeout_seconds"],
        "DEFAULT_WEB_TIMEOUT_SECONDS": web["default_timeout_seconds"],
        "MAX_WEB_TIMEOUT_SECONDS": web["max_timeout_seconds"],
        "MAX_WEBPAGE_CHARS": web["max_webpage_chars"],
        "DEFAULT_DEBUG_MAX_CHARS": debug["default_max_chars"],
        "TRAIN_DEFAULT_EPOCHS": training["default_epochs"],
        "TRAIN_DEFAULT_BATCH_SIZE": training["default_batch_size"],
        "TRAIN_DEFAULT_CHECKPOINT_EVERY": training["default_checkpoint_every"],
        "EVAL_LAYER_TIMEOUT_SECONDS": training["eval_layer_timeout_seconds"],
        "GRADE_TIMEOUT_SECONDS": training["grade_timeout_seconds"],
    })

    workspace_root = Path.cwd().resolve()
    agent_state_dir = _path_override(
        "LANGBRIDGE_AGENT_STATE_DIR",
        paths.get("agent_state_dir"),
        workspace_root / "agent-state",
    )
    globals().update({
        "WORKSPACE_ROOT": workspace_root,
        "AGENT_STATE_DIR": agent_state_dir,
        "PROJECT_MEMORY_PATH": _path_override(
            "LANGBRIDGE_PROJECT_MEMORY_PATH",
            paths.get("project_memory_path"),
            workspace_root / ".langbridge" / "memory.md",
        ),
        "USER_MEMORY_PATH": _path_override(
            "LANGBRIDGE_USER_MEMORY_PATH",
            paths.get("user_memory_path"),
            CONFIG_DIR / "memory.md",
        ),
        # Sessions live under the langbridge-code checkout, grouped by project
        # (the directory the CLI was launched from): artifacts/{project}/{session}.
        "ARTIFACTS_DIR": _path_override(
            "LANGBRIDGE_ARTIFACTS_DIR",
            paths.get("artifacts_dir"),
            INSTALL_ROOT / "artifacts" / workspace_root.name,
        ),
    })


_bind(load_config())


_PROVIDER_ENV = {
    "moonshot": ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
}

PROVIDER_LABELS = {
    "moonshot": "Moonshot/Kimi",
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
}


def active_api_provider():
    cfg = load_config()
    return os.environ.get("LANGBRIDGE_API_PROVIDER", cfg.get("api", {}).get("provider", "openai"))


def _explicit_provider():
    """Provider explicitly chosen via env or the user's own config, else None."""
    env = os.environ.get("LANGBRIDGE_API_PROVIDER")
    if env:
        return env
    if USER_CONFIG_PATH.exists():
        user_cfg = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
        return (user_cfg.get("api") or {}).get("provider")
    return None


def choose_api_provider():
    """Return the active provider, asking interactively on first run.

    The choice is saved to the user config, so this only prompts once.
    Non-interactive runs (no TTY) silently use the packaged default.
    """
    explicit = _explicit_provider()
    if explicit:
        return explicit
    if not sys.stdin.isatty():
        return active_api_provider()

    options = tuple(PROVIDER_LABELS)
    default = active_api_provider()
    default_index = options.index(default) + 1 if default in options else 1
    print("Select API provider:")
    for number, name in enumerate(options, 1):
        print(f"  {number}) {PROVIDER_LABELS[name]} ({name})")
    raw = input(f"Choice [1-{len(options)}, default {default_index}]: ").strip()
    try:
        provider = options[int(raw) - 1] if raw else options[default_index - 1]
    except (ValueError, IndexError):
        provider = options[default_index - 1]

    save_user_config({"api": {"provider": provider}})
    _bind(load_config())  # re-resolve DEFAULT_MODEL / API_BASE_URL for the choice
    return provider


def model_for_agent(role, default=None):
    """Model for one agent role (explorer/planner/worker/reviewer).

    Resolution: LANGBRIDGE_MODEL env (global override) > per-role entry in the
    provider's agent_models config > the session default model.
    """
    env = os.environ.get("LANGBRIDGE_MODEL")
    if env:
        return env
    return AGENT_MODELS.get(role) or default or DEFAULT_MODEL


def _api_keys_from_config(cfg=None):
    cfg = cfg or load_config()
    return {k: v for k, v in (cfg.get("api_keys") or {}).items() if v}


def save_api_key(api_key, provider=None):
    provider = provider or active_api_provider()
    save_user_config({"api_keys": {provider: api_key}})


def load_api_key(provider=None):
    provider = provider or choose_api_provider()

    for env_name in _PROVIDER_ENV.get(provider, ()):
        api_key = os.environ.get(env_name)
        if api_key:
            return api_key

    api_key = _api_keys_from_config().get(provider)
    if api_key:
        return api_key

    label = PROVIDER_LABELS.get(provider, provider)
    api_key = getpass.getpass(f"Enter {label} API key: ")
    save_api_key(api_key, provider)
    return api_key
