"""Fork instructions — bodies live in sibling ``*.md`` files."""
from __future__ import annotations

from langbridge_code.prompt.load import load_prompt

MEMORY_WRITER_INSTRUCTION = load_prompt("fork/memory_writer.md").rstrip("\n")
SESSION_PROGRESS_TEMPLATE = load_prompt("fork/session_progress_template.md")
TASK_PROGRESS_TEMPLATE = load_prompt("fork/task_progress_template.md")


def build_progress_note_instruction(
    *,
    notes_path: str,
    current_notes: str,
    task: bool = False,
) -> str:
    """Build the Session-Memory-style update prompt for one progress.md file."""
    role = "this task" if task else "this session"
    # Use replace (not str.format): current_notes may contain literal braces.
    return (
        load_prompt("fork/progress_note_instruction.md")
        .replace("{role}", role)
        .replace("{notes_path}", notes_path)
        .replace("{current_notes}", current_notes)
        .rstrip("\n")
    )


# Back-compat names used by older imports / tests that only needed "an instruction".
NOTE_FORK_INSTRUCTION = build_progress_note_instruction(
    notes_path="progress.md",
    current_notes=SESSION_PROGRESS_TEMPLATE.strip(),
    task=False,
)
TASK_NOTE_FORK_INSTRUCTION = build_progress_note_instruction(
    notes_path="progress.md",
    current_notes=TASK_PROGRESS_TEMPLATE.strip(),
    task=True,
)

__all__ = [
    "MEMORY_WRITER_INSTRUCTION",
    "NOTE_FORK_INSTRUCTION",
    "SESSION_PROGRESS_TEMPLATE",
    "TASK_NOTE_FORK_INSTRUCTION",
    "TASK_PROGRESS_TEMPLATE",
    "build_progress_note_instruction",
]
