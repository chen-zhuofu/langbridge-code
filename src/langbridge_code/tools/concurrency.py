"""Per-call concurrency safety, mirroring Claude Code's ``isConcurrencySafe(input)``.

Safety is a property of the *call*, not the tool name: ``bash`` with
``git log`` is safe to run alongside reads, ``bash`` with ``rm -rf`` is not.
Unknown tools fail closed (not safe).
"""
from __future__ import annotations

import json

# Read-only regardless of input. agent_explorer is read-only by construction;
# agent_worker runs in its own isolated worktree.
_ALWAYS_SAFE_TOOL_NAMES = frozenset(
    {
        "glob",
        "read_file",
        "grep",
        "read_webpage",
        "read_skill",
        "agent_explorer",
        "agent_worker",
    }
)


def is_concurrency_safe(name: str, arguments: dict) -> bool:
    """True when this call cannot mutate shared state (safe to run in parallel)."""
    if name in _ALWAYS_SAFE_TOOL_NAMES:
        return True
    if name == "bash":
        from langbridge_code.tools.execution import bash_write_guard

        command = (arguments or {}).get("command") or ""
        return bool(command.strip()) and bash_write_guard(command) is None
    return False


def call_is_concurrency_safe(call: dict) -> bool:
    """Same check for a raw function_call item (JSON-encoded arguments)."""
    try:
        arguments = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(arguments, dict):
        return False
    return is_concurrency_safe(str(call.get("name") or ""), arguments)
