"""Agent role system prompts."""

from langbridge_code.prompt.system.explorer import EXPLORER_PROMPT, explorer_system_prompt
from langbridge_code.prompt.system.langbridge import LANGBRIDGE_PROMPT, langbridge_system_prompt
from langbridge_code.prompt.system.planner import PLANNER_PROMPT, planner_system_prompt
from langbridge_code.prompt.system.reviewer import REVIEWER_ENGINEER_PROMPT, reviewer_system_prompt
from langbridge_code.prompt.system.worker import WORKER_ENGINEER_PROMPT, worker_system_prompt

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
