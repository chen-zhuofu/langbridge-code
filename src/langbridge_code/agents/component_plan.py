"""The component_task_plan: L5's durable memory for one HARD component_task.

L5 splits a HARD component_task into a checklist of technical_sub_tasks (the last
one always an integration test) and conquers them one at a time. The checklist
lives in a file named uniquely after the component_task, so a later Ralph turn can
find it again, see which sub-tasks are already done, and pick up where it left off.

The format is a plain markdown checklist:

    # Component task plan: <task>

    - [ ] first technical_sub_task
    - [x] a finished one
    - [ ] integration test

A checked box means that sub-task passed L3 review.
"""

import re

from langbridge_cli.settings import COMPONENT_PLAN_DIR


_CHECKBOX = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+?)\s*$")


def slugify(task):
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
    return slug[:60] or "component"


def component_plan_path(task):
    return COMPONENT_PLAN_DIR / f"{slugify(task)}.md"


def read_component_plan(task):
    path = component_plan_path(task)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_component_plan(task, content):
    COMPONENT_PLAN_DIR.mkdir(parents=True, exist_ok=True)
    component_plan_path(task).write_text(content, encoding="utf-8")


def parse_sub_tasks(content):
    sub_tasks = []
    for line in content.splitlines():
        match = _CHECKBOX.match(line)
        if match:
            done = match.group(1).lower() == "x"
            sub_tasks.append((match.group(2).strip(), done))
    return sub_tasks


def render_component_plan(task, sub_tasks):
    lines = [f"# Component task plan: {task}", ""]
    for text, done in sub_tasks:
        box = "x" if done else " "
        lines.append(f"- [{box}] {text}")
    return "\n".join(lines) + "\n"


def next_unfinished_index(sub_tasks):
    for index, (_, done) in enumerate(sub_tasks):
        if not done:
            return index
    return None


def replace_sub_task(sub_tasks, index, new_items):
    """Replace one unfinished sub-task with smaller unchecked items."""
    _, done = sub_tasks[index]
    if done:
        return sub_tasks
    head = sub_tasks[:index]
    tail = sub_tasks[index + 1 :]
    return head + [(text, False) for text in new_items] + tail
