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
MAX_PM_LOOPS = 20
MAX_L4_L3_TURNS = 5
# Hard cap on Ralph turns for one HARD component_task handled by L5: each turn
# conquers one technical_sub_task. Stops a component from looping forever.
MAX_L5_RALPH_TURNS = 8
# Per-loop wall-clock budgets (seconds). Generous on purpose: they exist to stop
# a runaway loop, not to cut short a normal run.
MAX_PM_SECONDS = 3600
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
# All agent state lives under one root, split by agent role. The PM keeps its
# todo_list and the user<->PM session history; L5 keeps its component_task_plans.
# Every role gets a worklog/ dir (see below).
AGENT_STATE_DIR = Path(os.environ.get("LANGBRIDGE_AGENT_STATE_DIR", WORKSPACE_ROOT / "agent-state"))
PM_STATE_DIR = AGENT_STATE_DIR / "pm"
L3_STATE_DIR = AGENT_STATE_DIR / "l3"
L4_STATE_DIR = AGENT_STATE_DIR / "l4"
L5_STATE_DIR = AGENT_STATE_DIR / "l5"
RUNS_DIR = Path(os.environ.get("LANGBRIDGE_RUNS_DIR", PM_STATE_DIR / "session-history"))
TODO_LIST_PATH = Path(os.environ.get("LANGBRIDGE_TODO_LIST_PATH", PM_STATE_DIR / "todo_list.md"))
# One component_task_plan file per HARD component_task; uniquely named so the next
# L5 Ralph turn can find the plan it left behind and continue where it stopped.
COMPONENT_PLAN_DIR = Path(os.environ.get("LANGBRIDGE_COMPONENT_PLAN_DIR", L5_STATE_DIR / "component-plans"))
# Per-role worklog dir. The L4<->L3 review negotiation is recorded under L4 and
# the L5<->L3 one under L5 (the worker that drives the review); pm/ and l3/ hold
# each role's own run output.
PM_WORKLOG_DIR = PM_STATE_DIR / "worklog"
L3_WORKLOG_DIR = L3_STATE_DIR / "worklog"
L4_WORKLOG_DIR = L4_STATE_DIR / "worklog"
L5_WORKLOG_DIR = L5_STATE_DIR / "worklog"
WRITE_TOOLS = {
    "create_file",
    "delete_file",
    "edit_file",
    "install_python_packages",
    "ask_l4_engineer",
    "ask_l5_engineer",
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
