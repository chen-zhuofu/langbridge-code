from langbridge_cli.tools import agents, execution, filesystem, packages, testing
from langbridge_cli.tool_schema import with_tool_purpose

TOOL_SCHEMAS = with_tool_purpose(
    filesystem.TOOL_SCHEMAS
    + testing.TOOL_SCHEMAS
    + packages.TOOL_SCHEMAS
    + execution.TOOL_SCHEMAS
    + agents.TOOL_SCHEMAS
)
TOOLS = filesystem.TOOLS | testing.TOOLS | packages.TOOLS | execution.TOOLS | agents.TOOLS

MAIN_TOOL_NAMES = {"list_dir", "find_files", "read_file", "search_files", "ask_l4_engineer"}
MAIN_TOOL_SCHEMAS = with_tool_purpose(
    [
        schema
        for schema in filesystem.TOOL_SCHEMAS + agents.TOOL_SCHEMAS
        if schema["name"] in MAIN_TOOL_NAMES
    ]
)
MAIN_TOOLS = {
    name: tool
    for name, tool in (filesystem.TOOLS | agents.TOOLS).items()
    if name in MAIN_TOOL_NAMES
}

__all__ = ["TOOL_SCHEMAS", "TOOLS", "MAIN_TOOL_SCHEMAS", "MAIN_TOOLS"]
