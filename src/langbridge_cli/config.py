import getpass
import json
import os
from pathlib import Path


DEFAULT_MODEL = "gpt-5.1-codex"
CONFIG_DIR = Path.home() / ".langbridge"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history"
MAX_AGENT_STEPS = 50
MAX_SPECIALIST_AGENT_STEPS = 30
MAX_RALPH_LOOPS = 20
MAX_L4_L3_TURNS = 5
# Per-loop wall-clock budgets (seconds). Generous on purpose: they exist to stop
# a runaway loop, not to cut short a normal run.
MAX_RALPH_SECONDS = 3600
MAX_AGENT_SECONDS = 1800
MAX_SPECIALIST_SECONDS = 900
MAX_L4_L3_SECONDS = 1800
# Hard context-size caps (estimated tokens) for the LLM step loops.
MAX_AGENT_CONTEXT_TOKENS = 120_000
MAX_SPECIALIST_CONTEXT_TOKENS = 120_000
MAX_TOOL_SUMMARY_OUTPUT_CHARS = 300
MAX_SESSION_CHOICES = 10
MAX_SESSION_SUMMARY_INPUT_CHARS = 4_000
COMPACT_WHEN_TOKENS_OVER = 60_000
RECENT_CONTEXT_TOKENS = 40_000
SUMMARY_TARGET_CHARS = 8_000
STALE_TOOL_OUTPUT_CHARS = 500
WORKSPACE_ROOT = Path.cwd().resolve()
RUNS_DIR = Path(os.environ.get("LANGBRIDGE_RUNS_DIR", WORKSPACE_ROOT / "session-history"))
TODO_LIST_PATH = Path(os.environ.get("LANGBRIDGE_TODO_LIST_PATH", WORKSPACE_ROOT / "todo_list.md"))
WRITE_TOOLS = {
    "create_file",
    "delete_file",
    "edit_file",
    "install_python_packages",
    "ask_l4_engineer",
}


def load_api_key():
    import os

    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return api_key

    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())["api_key"]

    api_key = getpass.getpass("Enter Codex API key: ")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"api_key": api_key}, indent=2))
    CONFIG_PATH.chmod(0o600)
    return api_key
