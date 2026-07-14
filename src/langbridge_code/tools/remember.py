"""remember tool: main agent writes long-term project/user memory in-turn."""

from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER

REMEMBER_TOOL_SCHEMA = {
    "type": "function",
    "name": "remember",
    "description": (
        "Save one durable memory for future sessions (main agent only). "
        "Each memory becomes a small markdown file plus an index line in "
        "memory.md; relevant files are prefetched into your <memory> block "
        "at the start of future tasks. scope=project → this repo's memory "
        "(conventions, standing decisions, where things are tracked, "
        "project-specific preferences). scope=user → facts about the person "
        "you are working with, valid across projects (general preferences, "
        "standing feedback) — about the human, never about yourself. Call it "
        "the moment you learn something durable. Reusing an existing title "
        "overwrites that entry — do that to correct stale memory. Not for "
        "task status (use note_progress) or anything stale next week."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": PURPOSE_PARAMETER,
            "scope": {
                "type": "string",
                "enum": ["project", "user"],
                "description": "project = this repo only; user = this person, all projects.",
            },
            "title": {
                "type": "string",
                "description": (
                    "Short title; becomes the filename and the memory.md index line."
                ),
            },
            "content": {
                "type": "string",
                "description": "The memory body: a few concise sentences.",
            },
        },
        "required": ["purpose", "scope", "title", "content"],
        "additionalProperties": False,
    },
}
