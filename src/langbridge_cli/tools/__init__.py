from langbridge_cli.tools import agents, execution, filesystem, packages, plan, skills, testing, web
from langbridge_cli.llm.tool_schema import with_tool_purpose

TOOL_SCHEMAS = with_tool_purpose(
    filesystem.TOOL_SCHEMAS
    + testing.TOOL_SCHEMAS
    + packages.TOOL_SCHEMAS
    + execution.TOOL_SCHEMAS
    + agents.TOOL_SCHEMAS
    + plan.TOOL_SCHEMAS
    + web.TOOL_SCHEMAS
    + skills.TOOL_SCHEMAS
)
TOOLS = filesystem.TOOLS | testing.TOOLS | packages.TOOLS | execution.TOOLS | agents.TOOLS | plan.TOOLS | web.TOOLS | skills.TOOLS

MAIN_TOOL_NAMES = {"list_dir", "glob", "read_file", "grep", "execute_program", "read_webpage", "ask_l4_engineer", "ask_l5_engineer", "update_plan"}
MAIN_TOOL_SCHEMAS = with_tool_purpose(
    [
        schema
        for schema in filesystem.TOOL_SCHEMAS + execution.TOOL_SCHEMAS + web.TOOL_SCHEMAS + agents.TOOL_SCHEMAS + plan.TOOL_SCHEMAS
        if schema["name"] in MAIN_TOOL_NAMES
    ]
)
MAIN_TOOLS = {
    name: tool
    for name, tool in (filesystem.TOOLS | execution.TOOLS | web.TOOLS | agents.TOOLS | plan.TOOLS).items()
    if name in MAIN_TOOL_NAMES
}

__all__ = ["TOOL_SCHEMAS", "TOOLS", "MAIN_TOOL_SCHEMAS", "MAIN_TOOLS"]
