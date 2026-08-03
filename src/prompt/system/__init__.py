"""Agent role system prompts — bodies live in sibling ``*.md`` files."""
from __future__ import annotations

import platform
from datetime import date

from langbridge_code.prompt.load import load_prompt

EXPLORER_PROMPT = load_prompt("system/explorer.md").rstrip("\n")
LANGBRIDGE_PROMPT = load_prompt("system/langbridge.md").rstrip("\n")
PLANNER_PROMPT = load_prompt("system/planner.md").rstrip("\n")
REVIEWER_ENGINEER_PROMPT = load_prompt("system/reviewer.md").rstrip("\n")
WORKER_ENGINEER_PROMPT = load_prompt("system/worker.md").rstrip("\n")


def _is_git_repo(path):
    return any((parent / ".git").exists() for parent in [path, *path.parents])


def langbridge_system_prompt():
    # Skills are injected per task as a <skill_index> context block, not here.
    # Environment placeholders in langbridge.md are filled once per call.
    from langbridge_code.agents.common.workspace import get_workspace_root

    cwd = get_workspace_root()
    return (
        LANGBRIDGE_PROMPT.replace("{cwd}", str(cwd))
        .replace("{is_git}", "yes" if _is_git_repo(cwd) else "no")
        .replace("{system}", platform.system())
        .replace("{release}", platform.release())
        .replace("{today}", date.today().isoformat())
    )


def explorer_system_prompt():
    return EXPLORER_PROMPT


def planner_system_prompt():
    return PLANNER_PROMPT


def reviewer_system_prompt(task_type="coding"):
    # task_type kept for call-site compatibility; only coding remains.
    return REVIEWER_ENGINEER_PROMPT


def worker_system_prompt(task_type="coding"):
    # task_type kept for call-site compatibility; only coding remains.
    return WORKER_ENGINEER_PROMPT


__all__ = [
    "EXPLORER_PROMPT",
    "LANGBRIDGE_PROMPT",
    "PLANNER_PROMPT",
    "REVIEWER_ENGINEER_PROMPT",
    "WORKER_ENGINEER_PROMPT",
    "explorer_system_prompt",
    "langbridge_system_prompt",
    "planner_system_prompt",
    "reviewer_system_prompt",
    "worker_system_prompt",
]
