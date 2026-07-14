"""note_progress tool: fork a note-writer on the live context to update progress.md."""

from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER

NOTE_PROGRESS_TOOL_SCHEMA = {
    "type": "function",
    "name": "note_progress",
    "description": (
        "Record session progress right now (main agent only). This forks a "
        "note-writer on your live context: it summarizes the work since the "
        "last progress note and appends it to progress.md — you do not write "
        "the note yourself. Call it whenever something meaningful just "
        "completed or was decided: a subtask finished and verified, a plan "
        "committed, a key discovery, a user decision. You decide when; do not "
        "wait for the turn to end. progress.md survives compaction — it is "
        "re-read into your <progress> block, so anything noted here is never "
        "lost when older rounds are compressed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": PURPOSE_PARAMETER,
        },
        "required": ["purpose"],
        "additionalProperties": False,
    },
}

NOTE_FORK_INSTRUCTION = """You are a forked progress note-writer for this session.
Summarize the work completed since the last progress note (see the <progress>
block and any earlier notes above — do not repeat them). Output 1-3 concrete
past-tense sentences: what was done, files/tests touched, decisions made, open
blockers. Output the note text only — no heading, no preamble."""
