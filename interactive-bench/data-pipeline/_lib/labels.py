"""Difficulty buckets from agent runtime seconds."""
from __future__ import annotations

# Fixed thresholds (seconds). Tunable later from corpus percentiles.
EASY_MAX = 10 * 60
MEDIUM_MAX = 45 * 60


def difficulty_from_runtime(agent_runtime_sec: float | None) -> str:
    if agent_runtime_sec is None:
        return "unknown"
    if agent_runtime_sec <= EASY_MAX:
        return "easy"
    if agent_runtime_sec <= MEDIUM_MAX:
        return "medium"
    return "hard"


def task_type_from_prompt_intents(intents: list[str] | None) -> str:
    """Map SWE-Chat prompt_intent labels → our task_type."""
    if not intents:
        return "other"
    labels = {str(x).lower() for x in intents if x}
    if "debug" in labels:
        return "bug_fix"
    if "refactor" in labels:
        return "refactor"
    if "create new code" in labels:
        return "feature"
    return "other"
