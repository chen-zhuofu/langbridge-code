from langbridge_code.tools import (
    execution,
    filesystem,
    merge_branch,
    skills,
    web,
)

FILE_READ_TOOL_NAMES = {
    "glob",
    "grep",
    "read_file",
}

FILE_WRITE_TOOL_NAMES = {
    "write",
    "Edit",
}

SHELL_TOOL_NAMES = {"bash", "powershell"}

TOOL_SCHEMAS = (
    filesystem.TOOL_SCHEMAS
    + execution.TOOL_SCHEMAS
    + web.TOOL_SCHEMAS
    + skills.TOOL_SCHEMAS
)
TOOLS = (
    filesystem.TOOLS
    | execution.TOOLS
    | web.TOOLS
    | skills.TOOLS
)

MAIN_TOOL_SCHEMAS = (
    filesystem.TOOL_SCHEMAS
    + execution.TOOL_SCHEMAS
    + merge_branch.TOOL_SCHEMAS
    + web.TOOL_SCHEMAS
    + skills.TOOL_SCHEMAS
)
MAIN_TOOL_NAMES = {schema["name"] for schema in MAIN_TOOL_SCHEMAS}
MAIN_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS
        | execution.TOOLS
        | merge_branch.TOOLS
        | web.TOOLS
        | skills.TOOLS
    ).items()
}

# The evaluator verifies only; keep state-mutating merge_branch out of its hands.
GOAL_VERIFICATION_TOOL_SCHEMAS = [
    schema for schema in MAIN_TOOL_SCHEMAS if schema["name"] != "merge_branch"
]
GOAL_VERIFICATION_TOOL_NAMES = {schema["name"] for schema in GOAL_VERIFICATION_TOOL_SCHEMAS}
GOAL_VERIFICATION_TOOLS = {
    name: tool for name, tool in MAIN_TOOLS.items() if name != "merge_branch"
}

__all__ = [
    "FILE_READ_TOOL_NAMES",
    "FILE_WRITE_TOOL_NAMES",
    "SHELL_TOOL_NAMES",
    "TOOL_SCHEMAS",
    "TOOLS",
    "MAIN_TOOL_SCHEMAS",
    "MAIN_TOOLS",
    "MAIN_TOOL_NAMES",
    "GOAL_VERIFICATION_TOOL_NAMES",
    "GOAL_VERIFICATION_TOOL_SCHEMAS",
    "GOAL_VERIFICATION_TOOLS",
]
