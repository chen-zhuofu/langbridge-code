"""memory_writer tool: fork a tool-using agent on the live conversation context."""

from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER


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
            "purpose": PURPOSE_PARAMETER,
        },
        "required": ["purpose"],
        "additionalProperties": False,
    },
}
