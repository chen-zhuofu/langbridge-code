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
    "write",
    "edit_file",
    "multi_edit",
    "apply_patch",
    "delete_file",
    "bash",
    "powershell",
    "git_commit",
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
    browser = cfg["tools"].get("browser", {})
    debug = cfg["tools"]["debug"]
    training = cfg["training"]
    paths = cfg.get("paths", {})
    api = cfg.get("api", {})

    globals().update({
        "DEFAULT_MODEL": os.environ.get("LANGBRIDGE_MODEL", cfg["model"]),
        "API_PROVIDER": os.environ.get("LANGBRIDGE_API_PROVIDER", api.get("provider", "openai")),
        "API_BASE_URL": os.environ.get("LANGBRIDGE_API_BASE_URL", api.get("base_url", "")),
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
        "MAX_EXPLORER_STEPS": agent.get(
            "max_explorer_steps",
            agent.get("max_specialist_agent_steps", 30),
        ),
        "MAX_EXPLORER_SECONDS": agent.get(
            "max_explorer_seconds",
            agent.get("max_specialist_seconds", 900),
        ),
        "MAX_WORKER_STEPS": agent.get(
            "max_worker_steps",
            agent.get("max_specialist_agent_steps", 30),
        ),
        "MAX_WORKER_SECONDS": agent.get(
            "max_worker_seconds",
            agent.get("max_specialist_seconds", 900),
        ),
        "MAX_REVIEWER_STEPS": agent.get(
            "max_reviewer_steps",
            agent.get("max_specialist_agent_steps", 30),
        ),
        "MAX_REVIEWER_SECONDS": agent.get(
            "max_reviewer_seconds",
            agent.get("max_specialist_seconds", 900),
        ),
        "MAX_WORKFLOW_SECONDS": agent.get("max_workflow_seconds", 3600),
        "MAX_PLANNER_STEPS": agent.get("max_planner_steps", 30),
        "MAX_PLANNER_SECONDS": agent.get("max_planner_seconds", 600),
        "MAX_CODER_REVIEWER_ROUNDS": agent.get(
            "max_worker_reviewer_steps",
            agent.get("max_worker_reviewer_rounds", agent.get("max_coder_reviewer_rounds", agent.get("max_agent_steps", 50))),
        ),
        "MAX_WORKER_REVIEWER_STEPS": agent.get(
            "max_worker_reviewer_steps",
            agent.get("max_worker_reviewer_rounds", agent.get("max_agent_steps", 50)),
        ),
        "MAX_WORKER_REVIEWER_ROUNDS": agent.get(
            "max_worker_reviewer_steps",
            agent.get("max_worker_reviewer_rounds", agent.get("max_coder_reviewer_rounds", agent.get("max_agent_steps", 50))),
        ),
        "MAX_CODER_REVIEWER_SECONDS": agent.get("max_worker_reviewer_seconds", agent.get("max_coder_reviewer_seconds", 1800)),
        "MAX_WORKER_REVIEWER_SECONDS": agent.get("max_worker_reviewer_seconds", agent.get("max_coder_reviewer_seconds", 1800)),
        "WORKFLOW_OUTER_MULTIPLIER": agent.get("workflow_outer_multiplier", 2),
        "MAX_PARALLEL_TOOL_CALLS": int(
            os.environ.get("LANGBRIDGE_MAX_PARALLEL_TOOL_CALLS", agent.get("max_parallel_tool_calls", 4))
        ),
        "MAX_PARALLEL_CODERS": int(
            os.environ.get(
                "LANGBRIDGE_MAX_PARALLEL_CODERS",
                agent.get("max_parallel_workers", agent.get("max_parallel_coders", 2)),
            )
        ),
        "MAX_PARALLEL_WORKERS": int(
            os.environ.get(
                "LANGBRIDGE_MAX_PARALLEL_WORKERS",
                agent.get("max_parallel_workers", agent.get("max_parallel_coders", 2)),
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
        "PROGRESS_MAX_FRACTION": float(context.get("progress_max_fraction", 0.4)),
        "TRACES_RESUME_MAX_FRACTION": float(context.get("traces_resume_max_fraction", 0.3)),
        "COMPACT_USE_LLM": context.get("compact_use_llm", True),
        "COMPACT_PROSE_TARGET_CHARS": int(context.get("compact_prose_target_chars", 16000)),
        "PROGRESS_NOTE_REMINDER_ROUNDS": int(context.get("progress_note_reminder_rounds", 10)),
        "CONTEXT_DEBUG_PERSIST": _env_bool(
            "LANGBRIDGE_CONTEXT_DEBUG_PERSIST",
            context.get("context_debug_persist", True),
        ),
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
        "BROWSER_DEFAULT_TIMEOUT_SECONDS": browser.get("default_timeout_seconds", 30),
        "BROWSER_MAX_TIMEOUT_SECONDS": browser.get("max_timeout_seconds", 120),
        "BROWSER_MAX_CONTENT_CHARS": browser.get("max_content_chars", 20000),
        "BROWSER_WAIT_AFTER_LOAD_MS": int(browser.get("wait_after_load_ms", 1000)),
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
    workflow_state_dir = agent_state_dir / "workflow"
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
        "ARTIFACTS_DIR": _path_override(
            "LANGBRIDGE_ARTIFACTS_DIR",
            paths.get("artifacts_dir"),
            PACKAGE_DIR / "artifacts",
        ),
        "RUNS_DIR": _path_override(
            "LANGBRIDGE_RUNS_DIR",
            paths.get("runs_dir") or paths.get("artifacts_dir"),
            PACKAGE_DIR / "artifacts",
        ),
        "TODO_LIST_PATH": _path_override(
            "LANGBRIDGE_TODO_LIST_PATH",
            paths.get("todo_list_path"),
            workflow_state_dir / "todo_list.md",
        ),
        "WORKFLOW_WORKLOG_DIR": workflow_state_dir / "worklog",
        "CODER_WORKLOG_DIR": agent_state_dir / "worker" / "worklog",
        "WORKER_WORKLOG_DIR": agent_state_dir / "worker" / "worklog",
        "REVIEWER_WORKLOG_DIR": agent_state_dir / "reviewer" / "worklog",
        "PLANNER_WORKLOG_DIR": agent_state_dir / "planner" / "worklog",
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
