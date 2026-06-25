from langbridge_cli.config import TODO_LIST_PATH


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "update_plan",
        "description": (
            "Write the full todo_list to the fixed todo_list document. "
            "Use it to record the component-level subtasks, their status "
            "(TODO / IN_PROGRESS / DONE), and a short note on where the work "
            "stands and what to do next. This overwrites the whole document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Full markdown content of the todo_list.",
                }
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


@tool("update_plan")
def update_plan(content):
    TODO_LIST_PATH.write_text(content, encoding="utf-8")
    return f"Updated todo_list ({len(content)} chars) at {TODO_LIST_PATH.name}."


def read_todo_list():
    if not TODO_LIST_PATH.exists():
        return ""
    return TODO_LIST_PATH.read_text(encoding="utf-8")
