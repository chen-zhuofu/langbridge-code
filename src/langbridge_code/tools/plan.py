from langbridge_cli.settings import TODO_LIST_PATH


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


def todo_list_path(run_log_path=None):
    """Per-session todo_list, so a brand-new session starts with a fresh one.

    The todo_list lives next to the session's history file, keyed by the session
    (run_log_path). A new session has no such file yet, so its context is empty.
    Falls back to the configured global path when there is no active session
    (e.g. unit tests).
    """
    if run_log_path is None:
        return TODO_LIST_PATH
    return run_log_path.with_name(f"{run_log_path.stem}.todo_list.md")


@tool("update_plan")
def update_plan(content, run_log_path=None):
    path = todo_list_path(run_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Updated todo_list ({len(content)} chars) at {path.name}."


def read_todo_list(run_log_path=None):
    path = todo_list_path(run_log_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
