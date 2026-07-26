"""memory_writer tool: fork a tool-using agent on the live conversation context."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from langbridge_code.prompt.fork import MEMORY_WRITER_INSTRUCTION
from langbridge_code.tools.common.description import DESCRIPTION_PARAMETER

MEMORY_WRITER_TOOL_SCHEMA = {
    "type": "function",
    "name": "memory_writer",
    "description": (
        "Fork a Memory Writer agent on the live conversation prefix. Use it as "
        "soon as durable identity, preferences, working feedback, references, or "
        "project context appears or is corrected. The fork reads both Memory "
        "indexes and uses ordinary file tools in a restricted Memory workspace "
        "to add, update, or delete entries, then exits. If nothing durable is "
        "worth saving, it makes no file changes."
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


def run_memory_writer_agent(api_key, model, messages) -> str:
    """Run a prefix-cache-friendly, tool-using Memory Writer fork."""
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


def schedule_memory_writer(api_key, model, messages) -> None:
    """Run the tool-using Memory Writer fork in a background thread."""
    if not (api_key and model and messages):
        return
    snapshot = list(messages)
    # threading.local TraceContext does not follow ThreadPool/daemon threads.
    from langbridge_code.util.trace_log import get_trace_context, set_trace_context

    parent_ctx = get_trace_context()

    def worker() -> None:
        previous = get_trace_context()
        if parent_ctx is not None:
            set_trace_context(parent_ctx)
        try:
            run_memory_writer_agent(api_key, model, snapshot)
        except Exception:
            pass
        finally:
            set_trace_context(previous)

    threading.Thread(target=worker, daemon=True, name="memory-writer").start()
