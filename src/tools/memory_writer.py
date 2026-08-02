"""memory_writer tool: fork a tool-using agent on the live conversation context.

The writer always runs in a background thread. Mid-turn tool calls and turn-end
hooks both schedule; callers get an immediate acknowledgement. Pending writers
are drained on process exit so eval/REPL shutdown does not truncate a write.
"""

from __future__ import annotations

import atexit
import tempfile
import threading
from pathlib import Path

from langbridge_code.prompt.fork import MEMORY_WRITER_INSTRUCTION
from langbridge_code.tools.common.description import DESCRIPTION_PARAMETER

MEMORY_WRITER_TOOL_SCHEMA = {
    "type": "function",
    "name": "memory_writer",
    "description": (
        "Schedule a Memory Writer fork on the live conversation prefix "
        "(what to store / not store: Session rules). Runs in the background; "
        "results appear on the next memory prefetch. No-ops if nothing durable "
        "is worth saving."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": DESCRIPTION_PARAMETER,
        },
        "required": ["description"],
        "additionalProperties": False,
    },
}

_SCHEDULED_MESSAGE = (
    "Memory Writer scheduled. It runs in the background; durable entries will "
    "be available on the next memory prefetch."
)

_pending_lock = threading.Lock()
_pending_threads: list[threading.Thread] = []
_atexit_registered = False


def run_memory_writer_agent(api_key, model, messages, *, memory_agent=None) -> str:
    """Run a prefix-cache-friendly, tool-using Memory Writer fork (blocking)."""
    from langbridge_code.agents.common.fork import fork_agent
    from langbridge_code.agents.common.workspace import workspace_scope
    from langbridge_code import memory as memory_mod
    from langbridge_code.tools import execution, filesystem

    available_schemas = filesystem.TOOL_SCHEMAS + execution.TOOL_SCHEMAS
    available_tools = filesystem.TOOLS | execution.TOOLS
    schemas = [
        schema
        for schema in available_schemas
        if schema["name"] in memory_mod.MEMORY_FILE_TOOL_NAMES
    ]
    tools = {
        name: available_tools[name] for name in memory_mod.MEMORY_FILE_TOOL_NAMES
    }
    agent = memory_agent if memory_agent is not None else memory_mod.current_memory_agent()
    with memory_mod.memory_agent_scope(agent):
        with memory_mod._memory_writer_lock:
            with tempfile.TemporaryDirectory(prefix="langbridge-memory-") as temporary:
                root = Path(temporary)
                memory_mod._stage_memory_workspace(root)
                with workspace_scope(root):
                    report = fork_agent(
                        api_key,
                        model,
                        list(messages),
                        MEMORY_WRITER_INSTRUCTION,
                        tool_schemas=schemas,
                        tools=tools,
                        label="Memory Writer",
                    )
                memory_mod._sync_staged_memories(root)
    return report or "Memory Writer finished."


def schedule_memory_writer(api_key, model, messages, *, memory_agent=None) -> str:
    """Run the Memory Writer fork in a background thread; return immediately."""
    if not (api_key and model and messages):
        return _SCHEDULED_MESSAGE
    from langbridge_code import memory as memory_mod

    snapshot = list(messages)
    agent = memory_agent if memory_agent is not None else memory_mod.current_memory_agent()
    # threading.local TraceContext does not follow ThreadPool/daemon threads.
    from langbridge_code.util.trace_log import get_trace_context, set_trace_context

    parent_ctx = get_trace_context()

    def worker() -> None:
        previous = get_trace_context()
        if parent_ctx is not None:
            set_trace_context(parent_ctx)
        try:
            run_memory_writer_agent(api_key, model, snapshot, memory_agent=agent)
        except Exception:
            pass
        finally:
            set_trace_context(previous)

    thread = threading.Thread(target=worker, daemon=True, name="memory-writer")
    with _pending_lock:
        _pending_threads.append(thread)
        _ensure_atexit()
    thread.start()
    return _SCHEDULED_MESSAGE


def drain_pending_memory_writers(timeout: float | None = 30.0) -> None:
    """Wait for in-flight Memory Writer forks (process exit / eval shutdown)."""
    with _pending_lock:
        pending = list(_pending_threads)
        _pending_threads.clear()
    for thread in pending:
        thread.join(timeout=timeout)


def _ensure_atexit() -> None:
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(drain_pending_memory_writers)
    _atexit_registered = True
