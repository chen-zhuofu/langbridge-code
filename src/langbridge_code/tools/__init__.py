from langbridge_code.tools import (
    browser,
    execution,
    filesystem,
    git_tools,
    lsp,
    skills,
    testing,
    todo_list,
    web,
)

FILE_READ_TOOL_NAMES = {
    "list_dir",
    "glob",
    "grep",
    "read_file",
    "read_many",
}

FILE_WRITE_TOOL_NAMES = {
    "write",
    "edit_file",
    "multi_edit",
    "apply_patch",
    "delete_file",
}

GIT_READ_TOOL_NAMES = {"git_status", "git_diff"}
GIT_WRITE_TOOL_NAMES = {"git_commit"}

SHELL_TOOL_NAMES = {"bash", "powershell"}

TOOL_SCHEMAS = (
    filesystem.TOOL_SCHEMAS
    + execution.TOOL_SCHEMAS
    + git_tools.TOOL_SCHEMAS
    + lsp.TOOL_SCHEMAS
    + testing.TOOL_SCHEMAS
    + todo_list.TOOL_SCHEMAS
    + web.TOOL_SCHEMAS
    + skills.TOOL_SCHEMAS
)
TOOLS = (
    filesystem.TOOLS
    | execution.TOOLS
    | git_tools.TOOLS
    | lsp.TOOLS
    | testing.TOOLS
    | todo_list.TOOLS
    | web.TOOLS
    | skills.TOOLS
)

MAIN_TOOL_SCHEMAS = (
    filesystem.TOOL_SCHEMAS
    + execution.TOOL_SCHEMAS
    + git_tools.TOOL_SCHEMAS
    + lsp.TOOL_SCHEMAS
    + testing.TOOL_SCHEMAS
    + todo_list.TOOL_SCHEMAS
    + web.TOOL_SCHEMAS
    + browser.TOOL_SCHEMAS
    + skills.TOOL_SCHEMAS
)
MAIN_TOOL_NAMES = {schema["name"] for schema in MAIN_TOOL_SCHEMAS}
MAIN_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS
        | execution.TOOLS
        | git_tools.TOOLS
        | lsp.TOOLS
        | testing.TOOLS
        | todo_list.TOOLS
        | web.TOOLS
        | browser.TOOLS
        | skills.TOOLS
    ).items()
}

GOAL_VERIFICATION_TOOL_NAMES = MAIN_TOOL_NAMES
GOAL_VERIFICATION_TOOL_SCHEMAS = list(MAIN_TOOL_SCHEMAS)
GOAL_VERIFICATION_TOOLS = dict(MAIN_TOOLS)

__all__ = [
    "FILE_READ_TOOL_NAMES",
    "FILE_WRITE_TOOL_NAMES",
    "GIT_READ_TOOL_NAMES",
    "GIT_WRITE_TOOL_NAMES",
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
