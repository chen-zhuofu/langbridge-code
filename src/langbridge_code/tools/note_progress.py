"""note_progress tool: fork a note-writer on the live context to update progress.md."""

from langbridge_code.prompt.fork import NOTE_FORK_INSTRUCTION, TASK_NOTE_FORK_INSTRUCTION
from langbridge_code.tools.common.description import DESCRIPTION_PARAMETER

NOTE_PROGRESS_TOOL_SCHEMA = {
    "type": "function",
    "name": "note_progress",
    "description": (
        "Record session progress right now (main agent only). This forks a "
        "note-writer on your live context: it summarizes the work since the "
        "last progress note and appends it to progress.md — you do not write "
        "the note yourself. Prefer calling it after subagent results and other "
        "milestones. The harness also auto-writes after too many silent rounds "
        "and again at turn end if anything is still unnoted. progress.md "
        "survives compaction — it is re-read into your <progress> block."
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

TASK_NOTE_PROGRESS_TOOL_SCHEMA = {
    "type": "function",
    "name": "note_progress",
    "description": (
        "Record progress on your assigned task right now. This forks a "
        "note-writer on your live context: it summarizes the work since the "
        "last note and appends it to this task's progress file — you do not "
        "write the note yourself. Prefer calling it after meaningful steps. "
        "The harness also auto-writes after too many silent rounds. The file "
        "survives compaction and is shown (as <progress>) to the next agent "
        "on this task."
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

__all__ = [
    "NOTE_FORK_INSTRUCTION",
    "NOTE_PROGRESS_TOOL_SCHEMA",
    "TASK_NOTE_FORK_INSTRUCTION",
    "TASK_NOTE_PROGRESS_TOOL_SCHEMA",
]
