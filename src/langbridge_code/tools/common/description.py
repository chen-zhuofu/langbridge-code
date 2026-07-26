"""Description field on tool calls — for trace/UI only, stripped before execution.

Exception: subagent dispatch tools (``agent_planner`` / ``agent_explorer`` /
``agent_worker``) take a *functional* ``description`` (short task title) that
must reach the tool function, so it is not stripped for them.
"""

TOOL_DESCRIPTION = "description"

DESCRIPTION_PARAMETER = {
    "type": "string",
    "description": "One short user-visible sentence explaining why this tool call is needed now.",
}

FUNCTIONAL_DESCRIPTION_TOOLS = frozenset(
    {"agent_planner", "agent_explorer", "agent_worker"}
)


def without_description(arguments, tool_name: str = ""):
    if not isinstance(arguments, dict):
        return arguments
    if tool_name in FUNCTIONAL_DESCRIPTION_TOOLS:
        return arguments
    return {name: value for name, value in arguments.items() if name != TOOL_DESCRIPTION}
