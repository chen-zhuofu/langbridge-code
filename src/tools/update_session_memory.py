"""update_session_memory tool: fork an Edit-restricted writer on the live context."""

from langbridge_code.prompt.fork import NOTE_FORK_INSTRUCTION, TASK_NOTE_FORK_INSTRUCTION
from langbridge_code.tools.common.description import DESCRIPTION_PARAMETER

UPDATE_SESSION_MEMORY_TOOL_SCHEMA = {
    "type": "function",
    "name": "update_session_memory",
    "description": (
        "Fork a note-writer that Edits session_memory.md in place (main agent "
        "only; you do not write the note). Call after every subagent return and "
        "other milestones (see Context management). Harness may also auto-write "
        "after silent rounds / turn end. Survives compaction via <session_memory>."
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

TASK_UPDATE_SESSION_MEMORY_TOOL_SCHEMA = {
    "type": "function",
    "name": "update_session_memory",
    "description": (
        "Record session memory for your assigned task right now. This forks a "
        "note-writer on your live context that uses Edit to update sections "
        "in this task's session_memory.md in place — you do not write the note "
        "yourself. Prefer calling it after meaningful steps. The harness also "
        "auto-writes after too many silent rounds. The file survives "
        "compaction and is shown (as <session_memory>) to the next agent on "
        "this task."
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

# Back-compat aliases (old symbol / tool names).
NOTE_PROGRESS_TOOL_SCHEMA = UPDATE_SESSION_MEMORY_TOOL_SCHEMA
TASK_NOTE_PROGRESS_TOOL_SCHEMA = TASK_UPDATE_SESSION_MEMORY_TOOL_SCHEMA

__all__ = [
    "NOTE_FORK_INSTRUCTION",
    "NOTE_PROGRESS_TOOL_SCHEMA",
    "TASK_NOTE_FORK_INSTRUCTION",
    "TASK_NOTE_PROGRESS_TOOL_SCHEMA",
    "TASK_UPDATE_SESSION_MEMORY_TOOL_SCHEMA",
    "UPDATE_SESSION_MEMORY_TOOL_SCHEMA",
]
