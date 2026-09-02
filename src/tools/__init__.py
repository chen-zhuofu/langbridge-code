from langbridge_code.tools import (
    browser,
    execution,
    filesystem,
    merge_branch,
    schedule,
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


def _allow_main_app_skills(tool):
    def wrapped(**arguments):
        return tool(_allow_app_skills=True, **arguments)

    return wrapped

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

MAIN_TOOL_SCHEMAS = skills.schemas_with_role(
    filesystem.TOOL_SCHEMAS
    + execution.TOOL_SCHEMAS
    + merge_branch.TOOL_SCHEMAS
    + browser.TOOL_SCHEMAS
    + schedule.TOOL_SCHEMAS
    + web.TOOL_SCHEMAS
    + skills.TOOL_SCHEMAS,
    "langbridge",
)
MAIN_TOOL_NAMES = {schema["name"] for schema in MAIN_TOOL_SCHEMAS}
_MAIN_TOOL_BASE = {
    name: tool
    for name, tool in (
        filesystem.TOOLS
        | execution.TOOLS
        | merge_branch.TOOLS
        | browser.TOOLS
        | schedule.TOOLS
        | web.TOOLS
        | skills.TOOLS
    ).items()
}
for _name in ("read_file", "write", "Edit"):
    _MAIN_TOOL_BASE[_name] = _allow_main_app_skills(_MAIN_TOOL_BASE[_name])
MAIN_TOOLS = skills.tools_with_role(
    _MAIN_TOOL_BASE,
    "langbridge",
)

# The evaluator verifies only; keep state-mutating tools out of its hands.
GOAL_VERIFICATION_TOOL_SCHEMAS = [
    schema
    for schema in MAIN_TOOL_SCHEMAS
    if schema["name"] not in {"merge_branch", "browser", "schedule"}
]
GOAL_VERIFICATION_TOOL_NAMES = {schema["name"] for schema in GOAL_VERIFICATION_TOOL_SCHEMAS}
GOAL_VERIFICATION_TOOLS = {
    name: tool
    for name, tool in MAIN_TOOLS.items()
    if name not in {"merge_branch", "browser", "schedule"}
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
