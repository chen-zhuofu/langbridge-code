"""Plan tools: read_plan for workers; update_plan/clear_plan for main agent only."""
import re

from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.common.todo_list import (
    clean_task_text,
    find_matching_unfinished_task,
    format_dependency_dispatch_guidance,
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
        "goal, task_type, completed [x] items, next [ ] items, and a Ready/Blocked "
        "dependency wave (based on <!-- depends: N --> markers). "
        "Call before agent_worker when the user continues, asks for status, or you "
        "need to dispatch work. Spawn one agent_worker per Ready item when several "
        "are ready; do not dispatch Blocked items early. Trust [x] items as already "
        "done — do not re-read source files or re-run tests just to verify completed "
        "tasks. Workers may call this for read-only context (Out of scope, Changes "
        "required); they still implement only the subtask in their assigned task."
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
        "not needed after a successful clear. Main agent only."
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

UPDATE_PLAN_TOOL_SCHEMA = {
    "type": "function",
    "name": "update_plan",
    "description": (
        "Write or replace the full session plan markdown. Main agent only. "
        "Call this after you have reviewed an agent_planner draft (or after you "
        "edited it yourself). If the draft has ambiguities, ask_user first — only "
        "commit when the plan matches what the user wants. Start content with "
        "<!-- task_type: coding --> or <!-- task_type: slide -->. Include Desired "
        "end state, Success criteria, Key discoveries, Out of scope, Current state, "
        "Design options, Open questions, Todo list with <!-- depends: ... --> and "
        "<!-- verify: ... --> on coding todos, and Changes required when known. "
        "Do not dispatch agent_worker until this tool has succeeded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": PURPOSE_PARAMETER,
            "content": {
                "type": "string",
                "description": "Full markdown content of the todo_list / plan.",
            },
        },
        "required": ["purpose", "content"],
        "additionalProperties": False,
    },
}

TOOL_SCHEMAS = [READ_PLAN_TOOL_SCHEMA, CLEAR_PLAN_TOOL_SCHEMA, UPDATE_PLAN_TOOL_SCHEMA]

TOOLS = {}

_PLAN_FENCE = re.compile(
    r"```(?:markdown|md)?\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


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
        guidance = format_dependency_dispatch_guidance(load_tasks(run_log_path))
        if guidance:
            sections.append(guidance)
    if run_log_path is not None:
        branches = worktree_mod.ready_branches(run_log_path)
        if branches:
            sections.append(
                "Ready feature branches (merge each yourself with merge_branch, one call per branch):\n"
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
    lines.append("Call agent_planner to draft a new plan, then update_plan to commit it.")
    return "\n".join(lines)


@tool("update_plan")
def update_plan(content, run_log_path=None):
    from langbridge_code.agents.common.todo_list import write_task_type_marker

    text = content or ""
    # Normalize task_type marker if the main agent echoed PLAN_TASK_TYPE style.
    lowered = text.lstrip().lower()
    if not lowered.startswith("<!-- task_type:"):
        if "plan_task_type: slide" in lowered or "plan_task_type: presentation" in lowered:
            text = write_task_type_marker(text, "slide")
        elif "plan_task_type: coding" in lowered:
            text = write_task_type_marker(text, "coding")
    path = write_todo_list(text, run_log_path)
    return f"Updated todo_list ({len(text)} chars) at {path.name}."


def extract_plan_markdown(report: str) -> str | None:
    """Pull full plan markdown from a planner final reply (fenced or # Plan heading)."""
    text = report or ""
    for match in _PLAN_FENCE.finditer(text):
        body = match.group("body").strip()
        if "## Todo list" in body or re.search(r"^\s*-\s*\[[ xX]\]", body, re.MULTILINE):
            return body
    start = text.find("# Plan")
    if start == -1:
        return None
    chunk = text[start:].strip()
    summary_at = chunk.rfind("\n## Summary\n")
    if summary_at > 0:
        todo_at = chunk.find("## Todo list")
        if todo_at != -1 and summary_at > todo_at:
            after_todo = chunk[todo_at:summary_at]
            if "- [ ]" in after_todo or "- [x]" in after_todo:
                chunk = chunk[:summary_at].strip()
    if "## Todo list" in chunk or re.search(r"^\s*-\s*\[[ xX]\]", chunk, re.MULTILINE):
        return chunk
    return None


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
