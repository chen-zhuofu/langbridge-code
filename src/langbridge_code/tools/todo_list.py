"""read_plan and clear_plan tools for the main agent; read_plan for worker context."""
from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.common.todo_list import (
    clean_task_text,
    find_matching_unfinished_task,
    load_tasks,
    mark_subtask_done_in_content,
    read_todo_list,
    unfinished_count,
    write_todo_list,
)
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER

READ_PLAN_TOOL_SCHEMA = {
    "type": "function",
    "name": "read_plan",
    "description": (
        "Read the session todo_list markdown. Fastest way to see project context: "
        "goal, task_type, completed [x] items, and next [ ] items. "
        "Call before agent_worker when the user continues, asks for status, or you "
        "need to dispatch work. Trust [x] items as already done — do not re-read "
        "source files or re-run tests just to verify completed tasks. "
        "Workers may call this for read-only context (Out of scope, Changes required); "
        "they still implement only the subtask in their assigned task."
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

CLEAR_PLAN_TOOL_SCHEMA = {
    "type": "function",
    "name": "clear_plan",
    "description": (
        "Delete the session todo_list so a new plan can be written. "
        "Call only after ask_user confirmed the user wants to replace or abandon "
        "the current unfinished plan (or when the plan is already empty). "
        "Then call agent_planner for the new project — replace_existing_plan is "
        "not needed after a successful clear."
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

TOOL_SCHEMAS = [READ_PLAN_TOOL_SCHEMA, CLEAR_PLAN_TOOL_SCHEMA]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


@tool("read_plan")
def read_plan(run_log_path=None):
    content = read_todo_list(run_log_path)
    sections: list[str] = []
    if not content.strip():
        sections.append("Todo list is empty.")
    else:
        sections.append(content)
    if run_log_path is not None:
        branches = worktree_mod.ready_branches(run_log_path)
        if branches:
            sections.append(
                "Ready feature branches (delegate agent_worker to merge each into main workspace):\n"
                + "\n".join(f"- {branch}" for branch in branches)
            )
    return "\n\n".join(sections)


@tool("clear_plan")
def clear_plan(run_log_path=None):
    content = read_todo_list(run_log_path)
    if not content.strip():
        return "Todo list is already empty."
    remaining = unfinished_count(load_tasks(run_log_path))
    path = write_todo_list("", run_log_path=run_log_path)
    lines = [f"Cleared todo_list ({path.name})."]
    if remaining:
        lines.append(f"Discarded {remaining} unchecked item(s).")
    lines.append("Call agent_planner to write a new plan.")
    return "\n".join(lines)


def _mark_subtask_complete(subtask, run_log_path=None):
    needle = (subtask or "").strip()
    if not needle:
        return "Tool error: subtask must be a non-empty string."
    content = read_todo_list(run_log_path)
    if not content.strip():
        return "Tool error: todo_list is empty."

    new_content, matched = mark_subtask_done_in_content(content, needle)
    if matched is None:
        tasks = load_tasks(run_log_path)
        unfinished = [clean_task_text(task.description) for task in tasks if task.unfinished]
        lines = [f"Tool error: no matching unchecked todo for {needle!r}."]
        if unfinished:
            lines.append("")
            lines.append("Unfinished items:")
            lines.extend(f"- {item}" for item in unfinished[:8])
        return "\n".join(lines)

    write_todo_list(new_content, run_log_path=run_log_path)
    tasks = load_tasks(run_log_path)
    remaining = unfinished_count(tasks)
    label = clean_task_text(matched.description)
    lines = [
        f"Marked complete: {label}",
        f"Remaining unchecked: {remaining}",
    ]
    if remaining == 0:
        lines.append("all_complete=true — you may report the full project finished to the user.")
    else:
        next_items = [clean_task_text(task.description) for task in tasks if task.unfinished][:3]
        lines.append("all_complete=false — dispatch the next unchecked subtask via agent_worker.")
        if next_items:
            lines.append("")
            lines.append("Next unchecked:")
            lines.extend(f"- {item}" for item in next_items)
    return "\n".join(lines)


def complete_subtask_after_review(task, run_log_path=None):
    """Mark the worker's subtask done after reviewer PASS. Idempotent."""
    needle = (task or "").strip()
    if not needle or run_log_path is None:
        return ""
    content = read_todo_list(run_log_path)
    if not content.strip():
        return ""

    tasks = load_tasks(run_log_path)
    if find_matching_unfinished_task(tasks, needle) is not None:
        return _mark_subtask_complete(needle, run_log_path=run_log_path)

    needle_clean = clean_task_text(needle).lower()
    if not needle_clean:
        return ""
    for item in tasks:
        if item.unfinished:
            continue
        desc = clean_task_text(item.description).lower()
        if desc == needle_clean or needle_clean in desc or desc in needle_clean:
            return f"Todo already complete: {clean_task_text(item.description)}"
    return ""
