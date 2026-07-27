"""User-task wrapper for headless eval agent runs.

Public benchmarks (SWE-bench et al.) are fed the raw problem statement so results
stay comparable with published numbers. Any behavioural guidance belongs in the
agent's own system prompt, not in the task text.
"""

TASK_WRAPPER = "{issue}"
