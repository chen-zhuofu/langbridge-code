from langbridge_code.skills import list_skills, load_skill
from langbridge_code.tools.common.description import DESCRIPTION_PARAMETER

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


def _available_lines(role=None):
    skills = list_skills(role=role) if role is not None else list_skills()
    if not skills:
        return "(none for this role)"
    return "\n".join(f"- {name}: {description}" for name, description in skills)


def read_skill_schema(role=None):
    """Build the read_skill tool schema, optionally scoped to one role dir."""
    return {
        "type": "function",
        "name": "read_skill",
        "description": (
            "Load a skill: a short playbook of guidelines for a kind of work. "
            "Call when one fits the current task, then follow it. Role playbooks "
            "may also be listed in your system prompt. To load a linked reference "
            "under a skill, pass the relative path "
            "(e.g. clean-code-guard/references/ai-failure-modes.md). "
            "Available skills:\n"
            + _available_lines(role)
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": DESCRIPTION_PARAMETER,
                "name": {
                    "type": "string",
                    "description": "Name (id) of the skill to load.",
                },
            },
            "required": ["description", "name"],
            "additionalProperties": False,
        },
    }


def read_skill_for(role):
    """Return a read_skill callable that only loads ``skills/<role>/``."""

    def read_skill(name):
        try:
            return load_skill(name, role=role)
        except FileNotFoundError:
            available = ", ".join(skill_name for skill_name, _ in list_skills(role=role))
            if not available:
                return (
                    f"Tool error: unknown skill '{name}'. "
                    f"No skills are available for role '{role}'."
                )
            return (
                f"Tool error: unknown skill '{name}'. "
                f"Available skills for this role: {available}"
            )

    return read_skill


def schemas_with_role(schemas, role):
    """Replace any read_skill schema with a role-scoped copy."""
    return [
        read_skill_schema(role) if schema.get("name") == "read_skill" else schema
        for schema in schemas
    ]


def tools_with_role(tools, role):
    """Return a tools dict whose read_skill is scoped to ``role``."""
    bound = dict(tools)
    if "read_skill" in bound:
        bound["read_skill"] = read_skill_for(role)
    return bound


# Default (unscoped) schema/tool kept for shared TOOL_SCHEMAS imports; agents
# should prefer read_skill_for / schemas_with_role so each role only sees its dir.
# We deliberately do NOT pin an `enum` of skill names here.
TOOL_SCHEMAS = [read_skill_schema()]


@tool("read_skill")
def read_skill(name):
    try:
        return load_skill(name)
    except FileNotFoundError:
        available = ", ".join(skill_name for skill_name, _ in list_skills())
        return f"Tool error: unknown skill '{name}'. Available skills: {available}"
