"""Fork instructions — bodies live in sibling ``*.md`` files."""
from __future__ import annotations

from langbridge_code.prompt.load import load_prompt

MEMORY_WRITER_INSTRUCTION = load_prompt("fork/memory_writer.md").rstrip("\n")
SESSION_MEMORY_TEMPLATE = load_prompt("fork/session_memory_template.md")
TASK_SESSION_MEMORY_TEMPLATE = load_prompt("fork/task_session_memory_template.md")

# Back-compat aliases.
SESSION_PROGRESS_TEMPLATE = SESSION_MEMORY_TEMPLATE
TASK_PROGRESS_TEMPLATE = TASK_SESSION_MEMORY_TEMPLATE


def build_session_memory_instruction(
    *,
    notes_path: str,
    current_notes: str,
    task: bool = False,
) -> str:
    """Build the session-memory update prompt for one session_memory.md file."""
    role = "this task" if task else "this session"
    # Use replace (not str.format): current_notes may contain literal braces.
    return (
        load_prompt("fork/session_memory_instruction.md")
        .replace("{role}", role)
        .replace("{notes_path}", notes_path)
        .replace("{current_notes}", current_notes)
        .rstrip("\n")
    )


build_progress_note_instruction = build_session_memory_instruction

# Back-compat names used by older imports / tests that only needed "an instruction".
NOTE_FORK_INSTRUCTION = build_session_memory_instruction(
    notes_path="session_memory.md",
    current_notes=SESSION_MEMORY_TEMPLATE.strip(),
    task=False,
)
TASK_NOTE_FORK_INSTRUCTION = build_session_memory_instruction(
    notes_path="session_memory.md",
    current_notes=TASK_SESSION_MEMORY_TEMPLATE.strip(),
    task=True,
)

__all__ = [
    "MEMORY_WRITER_INSTRUCTION",
    "NOTE_FORK_INSTRUCTION",
    "SESSION_MEMORY_TEMPLATE",
    "SESSION_PROGRESS_TEMPLATE",
    "TASK_NOTE_FORK_INSTRUCTION",
    "TASK_PROGRESS_TEMPLATE",
    "TASK_SESSION_MEMORY_TEMPLATE",
    "build_progress_note_instruction",
    "build_session_memory_instruction",
]
