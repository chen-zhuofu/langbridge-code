"""Parse and update the session todo_list markdown."""
import re
from dataclasses import dataclass

from langbridge_code.tools.plan import read_todo_list, update_plan

_TASK_LINE = re.compile(
    r"^\s*-\s*\[(?P<done>[ xX])\]\s*\[(?P<kind>coding|presentation)\]\s*(?P<text>.+?)\s*$"
)


@dataclass
class TodoTask:
    description: str
    task_type: str  # coding | presentation
    done: bool = False
    note: str = ""

    @property
    def unfinished(self):
        return not self.done


def parse_todo_list(content: str) -> list[TodoTask]:
    tasks: list[TodoTask] = []
    current: TodoTask | None = None
    for line in (content or "").splitlines():
        match = _TASK_LINE.match(line)
        if match:
            current = TodoTask(
                description=match.group("text").strip(),
                task_type=match.group("kind").strip().lower(),
                done=match.group("done").strip().lower() == "x",
            )
            tasks.append(current)
            continue
        if current is not None and line.strip().lower().startswith("note:"):
            current.note = line.split(":", 1)[1].strip()
    return tasks


def render_todo_list(tasks: list[TodoTask], title: str = "Todo") -> str:
    lines = [f"# {title}", ""]
    for task in tasks:
        mark = "x" if task.done else " "
        lines.append(f"- [{mark}] [{task.task_type}] {task.description}")
        if task.note:
            lines.append(f"  note: {task.note}")
    return "\n".join(lines).strip() + "\n"


def load_tasks(run_log_path) -> list[TodoTask]:
    return parse_todo_list(read_todo_list(run_log_path))


def save_tasks(tasks: list[TodoTask], run_log_path, title: str = "Todo") -> str:
    content = render_todo_list(tasks, title=title)
    update_plan(content, run_log_path=run_log_path)
    return content


def first_unfinished(tasks: list[TodoTask]) -> TodoTask | None:
    for task in tasks:
        if task.unfinished:
            return task
    return None


def unfinished_count(tasks: list[TodoTask]) -> int:
    return sum(1 for task in tasks if task.unfinished)


def mark_done(tasks: list[TodoTask], target: TodoTask) -> None:
    for task in tasks:
        if task is target or (
            task.description == target.description and task.task_type == target.task_type
        ):
            task.done = True
            return


def replace_task(tasks: list[TodoTask], target: TodoTask, replacements: list[TodoTask]) -> list[TodoTask]:
    out: list[TodoTask] = []
    replaced = False
    for task in tasks:
        if not replaced and task.description == target.description and task.task_type == target.task_type:
            out.extend(replacements)
            replaced = True
            continue
        out.append(task)
    if not replaced:
        out.extend(replacements)
    return out


def single_task(description: str, task_type: str = "coding") -> list[TodoTask]:
    return [TodoTask(description=description.strip(), task_type=task_type)]
