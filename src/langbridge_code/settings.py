"""Load LangBridge settings from config.json.

Defaults ship with the package (langbridge_code/config.json).
Per-user overrides live at ~/.langbridge-code/config.json (falls back to ~/.langbridge).
Environment variables still override secrets, model, and runtime paths.
"""
import getpass
import json
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "config.json"


def _default_config_dir() -> Path:
    new_dir = Path.home() / ".langbridge-code"
    legacy_dir = Path.home() / ".langbridge"
    if new_dir.exists() or not legacy_dir.exists():
        return new_dir
    return legacy_dir


CONFIG_DIR = _default_config_dir()
USER_CONFIG_PATH = CONFIG_DIR / "config.json"
# Back-compat alias used by main.py / TUI.
CONFIG_PATH = USER_CONFIG_PATH
HISTORY_PATH = CONFIG_DIR / "history"

WRITE_TOOLS = {
    "create_file",
    "delete_file",
    "edit_file",
    "bash",
}


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


def _bind(cfg):
    agent = cfg["agent"]
    context = cfg["context"]
    fs = cfg["tools"]["filesystem"]
    execution = cfg["tools"]["execution"]
    testing = cfg["tools"]["testing"]
    web = cfg["tools"]["web"]
    debug = cfg["tools"]["debug"]
    training = cfg["training"]
    paths = cfg.get("paths", {})
    api = cfg.get("api", {})

    globals().update({
        "DEFAULT_MODEL": os.environ.get("LANGBRIDGE_MODEL", cfg["model"]),
        "API_PROVIDER": os.environ.get("LANGBRIDGE_API_PROVIDER", api.get("provider", "openai")),
        "API_BASE_URL": os.environ.get("LANGBRIDGE_API_BASE_URL", api.get("base_url", "")),
        "DEFAULT_MAX_GUIDANCE": cfg["max_guidance"],
        "MAX_AGENT_STEPS": agent["max_agent_steps"],
        "MAX_SPECIALIST_AGENT_STEPS": agent["max_specialist_agent_steps"],
        "MAX_SPECIALIST_SECONDS": agent["max_specialist_seconds"],
        "MAX_WORKFLOW_SECONDS": agent.get("max_workflow_seconds", 3600),
        "MAX_PLANNER_STEPS": agent.get("max_planner_steps", 30),
        "MAX_PLANNER_SECONDS": agent.get("max_planner_seconds", 600),
        "MAX_CODER_REVIEWER_ROUNDS": agent.get("max_coder_reviewer_rounds", 5),
        "MAX_CODER_REVIEWER_SECONDS": agent.get("max_coder_reviewer_seconds", 1800),
        "MAX_PRESENTER_STEPS": agent.get("max_presenter_steps", 30),
        "MAX_PRESENTER_SECONDS": agent.get("max_presenter_seconds", 900),
        "WORKFLOW_OUTER_MULTIPLIER": agent.get("workflow_outer_multiplier", 2),
        # Back-compat aliases for training/eval until fully migrated.
        "MAX_L4_L3_TURNS": agent.get("max_coder_reviewer_rounds", 5),
        "MAX_L4_L3_SECONDS": agent.get("max_coder_reviewer_seconds", 1800),
        "MAX_PM_LOOPS": 20,
        "MAX_PM_SECONDS": agent.get("max_workflow_seconds", 3600),
        "MAX_AGENT_SECONDS": agent.get("max_workflow_seconds", 3600),
        "MAX_L5_RALPH_TURNS": 0,
        "MAX_AGENT_CONTEXT_TOKENS": context["max_agent_context_tokens"],
        "MAX_SPECIALIST_CONTEXT_TOKENS": context["max_specialist_context_tokens"],
        "MAX_TOOL_SUMMARY_OUTPUT_CHARS": context["max_tool_summary_output_chars"],
        "MAX_SESSION_CHOICES": context["max_session_choices"],
        "MAX_SESSION_SUMMARY_INPUT_CHARS": context["max_session_summary_input_chars"],
        "COMPACT_WHEN_TOKENS_OVER": context["compact_when_tokens_over"],
        "RECENT_CONTEXT_TOKENS": context["recent_context_tokens"],
        "SUMMARY_TARGET_CHARS": context["summary_target_chars"],
        "STALE_TOOL_OUTPUT_CHARS": context["stale_tool_output_chars"],
        "COMPACT_TOOL_STEPS_KEEP": context.get("compact_tool_steps_keep", 2),
        "COMPACT_RECENT_FILES_KEEP": context.get("compact_recent_files_keep", 5),
        "COMPACT_LOOP_FRACTION": context.get("compact_loop_fraction", 0.5),
        "COMPACT_USE_LLM": context.get("compact_use_llm", True),
        "COMPACT_LLM_INPUT_CHARS": context.get("compact_llm_input_chars", 24000),
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
    pm_state_dir = agent_state_dir / "pm"
    workflow_state_dir = agent_state_dir / "workflow"
    globals().update({
        "WORKSPACE_ROOT": workspace_root,
        "AGENT_STATE_DIR": agent_state_dir,
        "WORKFLOW_STATE_DIR": workflow_state_dir,
        "PM_STATE_DIR": pm_state_dir,
        "L3_STATE_DIR": agent_state_dir / "reviewer",
        "L4_STATE_DIR": agent_state_dir / "coder",
        "L5_STATE_DIR": agent_state_dir / "l5",
        "CODER_STATE_DIR": agent_state_dir / "coder",
        "REVIEWER_STATE_DIR": agent_state_dir / "reviewer",
        "PLANNER_STATE_DIR": agent_state_dir / "planner",
        "PRESENTER_STATE_DIR": agent_state_dir / "presenter",
        "RUNS_DIR": _path_override(
            "LANGBRIDGE_RUNS_DIR",
            paths.get("runs_dir"),
            pm_state_dir / "session-history",
        ),
        "TODO_LIST_PATH": _path_override(
            "LANGBRIDGE_TODO_LIST_PATH",
            paths.get("todo_list_path"),
            pm_state_dir / "todo_list.md",
        ),
        "COMPONENT_PLAN_DIR": _path_override(
            "LANGBRIDGE_COMPONENT_PLAN_DIR",
            paths.get("component_plan_dir"),
            agent_state_dir / "l5" / "component-plans",
        ),
        "PM_WORKLOG_DIR": workflow_state_dir / "worklog",
        "L3_WORKLOG_DIR": agent_state_dir / "reviewer" / "worklog",
        "L4_WORKLOG_DIR": agent_state_dir / "coder" / "worklog",
        "L5_WORKLOG_DIR": agent_state_dir / "l5" / "worklog",
        "CODER_WORKLOG_DIR": agent_state_dir / "coder" / "worklog",
        "REVIEWER_WORKLOG_DIR": agent_state_dir / "reviewer" / "worklog",
        "PLANNER_WORKLOG_DIR": agent_state_dir / "planner" / "worklog",
        "PRESENTER_WORKLOG_DIR": agent_state_dir / "presenter" / "worklog",
        "OPTIMIZER_TRACE_DIR": workflow_state_dir / "optimizer-traces",
    })


_bind(load_config())


_PROVIDER_ENV = {
    "moonshot": ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
}


def active_api_provider():
    cfg = load_config()
    return os.environ.get("LANGBRIDGE_API_PROVIDER", cfg.get("api", {}).get("provider", "openai"))


def _api_keys_from_config(cfg=None):
    cfg = cfg or load_config()
    keys = {k: v for k, v in (cfg.get("api_keys") or {}).items() if v}
    legacy = cfg.get("api_key")
    if legacy:
        provider = os.environ.get(
            "LANGBRIDGE_API_PROVIDER",
            cfg.get("api", {}).get("provider", "openai"),
        )
        keys.setdefault(provider, legacy)
    return keys


def save_api_key(api_key, provider=None):
    provider = provider or active_api_provider()
    save_user_config({"api_keys": {provider: api_key}})


def load_api_key(provider=None):
    provider = provider or active_api_provider()

    for env_name in _PROVIDER_ENV.get(provider, ()):
        api_key = os.environ.get(env_name)
        if api_key:
            return api_key

    api_key = _api_keys_from_config().get(provider)
    if api_key:
        return api_key

    label = "Moonshot/Kimi" if provider == "moonshot" else "OpenAI"
    api_key = getpass.getpass(f"Enter {label} API key: ")
    save_api_key(api_key, provider)
    return api_key
