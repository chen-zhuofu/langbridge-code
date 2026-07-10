"""Worker↔Reviewer subagent loop and agent_worker tool implementation."""
import json
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from langbridge_code.agents.common import control
from langbridge_code.agents.common.limits import now, over_context_budget, over_time_budget
from langbridge_code.agents.common.phases import emit_phase
from langbridge_code.agents.system_prompt import worker_system_prompt, reviewer_system_prompt
from langbridge_code.llm.client import create_model_response
from langbridge_code.llm.parse import extract_output_text, print_step_trace
from langbridge_code.util.agent_worklog import (
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
    write_worklog_step,
)
from langbridge_code.context.common.budget import prepare_agent_messages
from langbridge_code.context.message import recent_chat_turns
from langbridge_code.context.agent_context import finish_step, init_agent_context
from langbridge_code.settings import (
    MAX_WORKER_REVIEWER_SECONDS,
    MAX_WORKER_REVIEWER_STEPS,
    MAX_REVIEWER_SECONDS,
    MAX_REVIEWER_STEPS,
    MAX_WORKER_SECONDS,
    MAX_WORKER_STEPS,
    PARALLEL_AGENTS_ENABLED,
    WORKSPACE_ROOT,
)
from langbridge_code.agents.common.todo_list import (
    TodoTask,
    clean_task_text,
    read_task_type,
    render_todo_list,
    resolve_single_worker_task,
    write_task_type_marker,
    write_todo_list,
)
from langbridge_code.tools import (
    FILE_READ_TOOL_NAMES,
    FILE_WRITE_TOOL_NAMES,
    GIT_READ_TOOL_NAMES,
    GIT_WRITE_TOOL_NAMES,
    SHELL_TOOL_NAMES,
    execution,
    filesystem,
    git_tools,
    lsp,
    skills,
    testing,
)
from langbridge_code.tools.agent_explorer import AGENT_EXPLORER_TOOL_SCHEMA, build_agent_explorer_tool
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER, without_purpose
from langbridge_code.skills import normalize_task_type
from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.common.workspace import workspace_scope
from langbridge_code.tools import todo_list as plan_tools
from langbridge_code.training.optimizer_trace import append_event

# --- Worker toolkits by task type ---

CODE_WORKER_TOOL_NAMES = (
    FILE_READ_TOOL_NAMES
    | FILE_WRITE_TOOL_NAMES
    | SHELL_TOOL_NAMES
    | GIT_READ_TOOL_NAMES
    | GIT_WRITE_TOOL_NAMES
    | {"run_tests", "read_skill", "read_plan", "lsp"}
)
CODE_WORKER_TOOL_SCHEMAS = [
    schema
    for schema in (
        filesystem.TOOL_SCHEMAS
        + execution.TOOL_SCHEMAS
        + git_tools.TOOL_SCHEMAS
        + lsp.TOOL_SCHEMAS
        + testing.TOOL_SCHEMAS
        + skills.TOOL_SCHEMAS
        + plan_tools.TOOL_SCHEMAS
    )
    if schema["name"] in CODE_WORKER_TOOL_NAMES
]
CODE_WORKER_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS
        | execution.TOOLS
        | git_tools.TOOLS
        | lsp.TOOLS
        | testing.TOOLS
        | skills.TOOLS
        | plan_tools.TOOLS
    ).items()
    if name in CODE_WORKER_TOOL_NAMES
}
WORKER_WRITE_TOOLS = FILE_WRITE_TOOL_NAMES | GIT_WRITE_TOOL_NAMES

SLIDE_WORKER_TOOL_NAMES = (
    FILE_READ_TOOL_NAMES
    | {"write", "edit_file", "multi_edit", "apply_patch"}
    | {"read_skill", "read_plan", "lsp"}
    | GIT_READ_TOOL_NAMES
)
SLIDE_WORKER_TOOL_SCHEMAS = [
    schema
    for schema in (
        filesystem.TOOL_SCHEMAS
        + git_tools.TOOL_SCHEMAS
        + lsp.TOOL_SCHEMAS
        + skills.TOOL_SCHEMAS
        + plan_tools.TOOL_SCHEMAS
    )
    if schema["name"] in SLIDE_WORKER_TOOL_NAMES
]
SLIDE_WORKER_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS | git_tools.TOOLS | lsp.TOOLS | skills.TOOLS | plan_tools.TOOLS
    ).items()
    if name in SLIDE_WORKER_TOOL_NAMES
}

SLIDE_REVIEWER_TOOL_NAMES = FILE_READ_TOOL_NAMES | {"read_skill", "lsp"} | GIT_READ_TOOL_NAMES
SLIDE_REVIEWER_TOOL_SCHEMAS = [
    schema
    for schema in filesystem.TOOL_SCHEMAS + git_tools.TOOL_SCHEMAS + lsp.TOOL_SCHEMAS + skills.TOOL_SCHEMAS
    if schema["name"] in SLIDE_REVIEWER_TOOL_NAMES
]
SLIDE_REVIEWER_TOOLS = {
    name: tool
    for name, tool in (filesystem.TOOLS | git_tools.TOOLS | lsp.TOOLS | skills.TOOLS).items()
    if name in SLIDE_REVIEWER_TOOL_NAMES
}

# --- Reviewer specialist tools ---

REVIEWER_TOOL_NAMES = FILE_READ_TOOL_NAMES | {"run_tests", "read_skill", "lsp"} | GIT_READ_TOOL_NAMES
REVIEWER_TOOL_SCHEMAS = [
    schema
    for schema in (
        filesystem.TOOL_SCHEMAS
        + git_tools.TOOL_SCHEMAS
        + lsp.TOOL_SCHEMAS
        + testing.TOOL_SCHEMAS
        + skills.TOOL_SCHEMAS
    )
    if schema["name"] in REVIEWER_TOOL_NAMES
]
REVIEWER_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS | git_tools.TOOLS | lsp.TOOLS | testing.TOOLS | skills.TOOLS
    ).items()
    if name in REVIEWER_TOOL_NAMES
}

_APPROVAL_LOCK = threading.Lock()
_WORKTREE_INDEX_LOCK = threading.Lock()
_worktree_index_by_session: dict[str, int] = {}

_INTEGRATION_MARKER = re.compile(r"<!--\s*integration\s*-->", re.IGNORECASE)
_PARALLEL_MARKER = re.compile(
    r"<!--\s*parallel(?:\s+paths:\s*(?P<paths>[^>]+))?\s*-->",
    re.IGNORECASE,
)
_VERIFY_MARKER = re.compile(r"<!--\s*verify:\s*(?P<cmd>[^>]+)\s*-->", re.IGNORECASE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def save_tasks(tasks: list[TodoTask], run_log_path, title: str = "Todo") -> str:
    content = render_todo_list(tasks, title=title)
    task_type = read_task_type(run_log_path)
    if task_type:
        content = write_task_type_marker(content, task_type)
    write_todo_list(content, run_log_path=run_log_path)
    return content


def mark_done(tasks: list[TodoTask], target: TodoTask) -> None:
    for task in tasks:
        if task is target or task.description == target.description:
            task.done = True
            return


def is_integration_task(task: TodoTask) -> bool:
    blob = f"{task.description}\n{task.note}"
    if _INTEGRATION_MARKER.search(blob):
        return True
    lowered = clean_task_description(task).lower()
    return (
        lowered.startswith("verify merged")
        or ("merge" in lowered and "branch" in lowered)
        or ("resolve" in lowered and "conflict" in lowered)
    )


def clean_task_description(task: TodoTask) -> str:
    return clean_task_text(task.description)


def is_parallel_prompt(task: str) -> bool:
    return bool(_PARALLEL_MARKER.search(task or ""))


def parallel_prompt_paths(task: str) -> str:
    match = _PARALLEL_MARKER.search(task or "")
    if not match:
        return ""
    return (match.group("paths") or "").strip()


def is_parallel_task(task: TodoTask) -> bool:
    blob = f"{task.description}\n{task.note}"
    return bool(_PARALLEL_MARKER.search(blob))


def parallel_task_paths(task: TodoTask) -> str:
    blob = f"{task.description}\n{task.note}"
    match = _PARALLEL_MARKER.search(blob)
    if not match:
        return ""
    return (match.group("paths") or "").strip()


def task_verify_command(task: TodoTask) -> str:
    blob = f"{task.description}\n{task.note}"
    match = _VERIFY_MARKER.search(blob)
    if not match:
        return ""
    return match.group("cmd").strip()


def parallel_implementation_tasks(tasks: list[TodoTask]) -> list[TodoTask]:
    return [
        task
        for task in tasks
        if task.unfinished and not is_integration_task(task) and is_parallel_task(task)
    ]


def next_parallel_batch(tasks: list[TodoTask], max_workers: int) -> list[TodoTask]:
    batch = parallel_implementation_tasks(tasks)
    if len(batch) < 2:
        return []
    return batch[: max(1, min(max_workers, len(batch)))]


def pending_integration_tasks(tasks: list[TodoTask]) -> list[TodoTask]:
    return [task for task in tasks if task.unfinished and is_integration_task(task)]


def _validate_merge_prompt_single_branch(task: str, run_log_path) -> str | None:
    branches = re.findall(r"lb/[\w/-]+", task or "", re.IGNORECASE)
    if len(branches) > 1:
        return (
            "merge prompt references multiple branches "
            f"({', '.join(branches)}); one branch per agent_worker call."
        )
    if len(branches) == 1 and run_log_path is not None:
        ready = worktree_mod.ready_branches(run_log_path)
        if ready and branches[0] not in ready and branches[0].lower() not in {b.lower() for b in ready}:
            return (
                f"branch {branches[0]!r} is not in ready branches. "
                "read_plan and merge one ready branch per call."
            )
    return None


def is_merge_task_prompt(task: str) -> bool:
    cleaned = clean_task_text(task).lower()
    if "git merge" in cleaned:
        return True
    if "merge" in cleaned and "branch" in cleaned:
        return True
    if "resolve" in cleaned and "conflict" in cleaned:
        return True
    return bool(re.search(r"\blb/", task or "", re.IGNORECASE) and "merge" in cleaned)


def _branches_named_in_task(run_log_path, task: str) -> list[str]:
    return [branch for branch in worktree_mod.ready_branches(run_log_path) if branch in (task or "")]


def _merge_task_context(run_log_path, task: str) -> str:
    branches = _branches_named_in_task(run_log_path, task) or worktree_mod.ready_branches(run_log_path)
    lines = [
        "Git merge task — work in the main workspace (not a parallel worktree).",
        "Run `git merge <branch>` for the assigned branch.",
        "If conflicts arise, resolve with edit_file, `git add`, then complete the merge.",
        "Run any verify check from the task before READY_FOR_REVIEW.",
    ]
    if branches:
        lines.append("Ready branches:")
        lines.extend(f"- {branch}" for branch in branches)
    return "\n".join(lines)


def integration_pending_message(tasks: list[TodoTask], completed: list[str], *, run_log_path=None) -> str:
    integration = pending_integration_tasks(tasks)
    lines = [
        "Implementation tasks are complete. Merge and integration run via agent_worker.",
        "",
        "Main agent next steps:",
        "1. Delegate agent_worker once per ready feature branch to merge it into the main workspace.",
        "2. If merge or conflict resolution fails, refine the task and re-dispatch agent_worker.",
        "3. When the tree is clean, delegate agent_worker for the integration verification todo.",
        "",
    ]
    if run_log_path is not None:
        branches = worktree_mod.ready_branches(run_log_path)
        if branches:
            lines.append("Ready branches to merge:")
            lines.extend(f"- {branch}" for branch in branches)
            lines.append("")
    if completed:
        lines.extend(["Completed implementation:", *[f"- {item}" for item in completed], ""])
    if integration:
        lines.append("Pending integration todo(s):")
        lines.extend(f"- {task.description}" for task in integration)
    return "\n".join(lines).strip()


AGENT_WORKER_TOOL_SCHEMA = {
    "type": "function",
    "name": "agent_worker",
    "description": (
        "Launch the worker-reviewer subagent for exactly one todo subtask. "
        "Pass a focused prompt for a single unchecked item from read_plan (description, "
        "verify comment, and relevant Changes required snippets). "
        "Rejected if the prompt lists multiple todos, checkboxes, or branches. "
        "Do not paste the entire plan or multiple todos — one subtask per call. "
        "Worker may read_plan for read-only context but implements only your assigned subtask. "
    "When read_plan shows multiple <!-- parallel --> todos, the main agent may call "
    "agent_worker several times in one turn; parallel tasks run in isolated git worktrees. "
    "After parallel work, read_plan lists ready branches — delegate agent_worker to merge "
    "each branch into the main workspace (one branch per call). "
    "On reviewer PASS the matched todo is marked complete automatically. "
    "On failure, call agent_planner to refine the plan; do not re-dispatch the "
        "same task until the plan changes. Returns a final summary only. "
        "Delegate integration verification via agent_worker when merges are complete."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": PURPOSE_PARAMETER,
            "prompt": {
                "type": "string",
                "description": "Full task description for the subagent.",
            },
            "description": {
                "type": "string",
                "description": "Short 3-5 word title for logging.",
            },
            "task_type": {
                "type": "string",
                "enum": ["coding", "slide"],
                "description": "Task type from read_plan (default coding).",
            },
        },
        "required": ["purpose", "prompt", "description"],
        "additionalProperties": False,
    },
}


def worker_user_prompt(_task, context, feedback):
    """Per-phase turn line for worker; assigned task is pinned separately."""
    parts: list[str] = []
    if feedback:
        parts.append(f"Reviewer feedback to address:\n{feedback}")
    if context:
        parts.append(f"Additional context:\n{context}")
    return "\n\n".join(parts)


def reviewer_user_prompt(_task, context):
    """Per-phase turn line for reviewer; assigned task is pinned separately."""
    if context:
        return f"Review context:\n{context}"
    return ""


class StepOutcome(str, Enum):
    TOOL = "tool"
    FINAL = "final"
    EXHAUSTED = "exhausted"
    TIMEOUT = "timeout"
    CONTEXT = "context"


@dataclass
class WorkerReviewerLoopBudget:
    """Shared step/time budget for one LangBridge → worker-reviewer loop."""

    max_steps: int
    used_steps: int = 0
    start_time: float = field(default_factory=now)
    max_seconds: float = MAX_WORKER_REVIEWER_SECONDS

    def steps_left(self) -> int:
        return max(0, self.max_steps - self.used_steps)

    def consume_step(self) -> None:
        self.used_steps += 1

    def timed_out(self) -> bool:
        return over_time_budget(self.start_time, self.max_seconds)

    def exhausted(self) -> bool:
        return self.steps_left() <= 0 or self.timed_out()


def worker_ready_for_review(report):
    first_line = report.strip().splitlines()[0].strip().lower() if report.strip() else ""
    return first_line == "worker_status: ready_for_review"


def reviewer_review_passed(report):
    first_line = report.strip().splitlines()[0].strip().lower() if report.strip() else ""
    return first_line == "review_verdict: pass"


def build_code_worker_toolkit(
    *,
    api_key,
    model,
    run_log_path=None,
    turn_id=None,
    trace_sink=None,
    phase_sink=None,
):
    tools = {
        **CODE_WORKER_TOOLS,
        "agent_explorer": build_agent_explorer_tool(
            api_key=api_key,
            model=model,
            run_log_path=run_log_path,
            turn_id=turn_id,
            trace_sink=trace_sink,
            phase_sink=phase_sink,
        ),
    }
    schemas = list(CODE_WORKER_TOOL_SCHEMAS) + [AGENT_EXPLORER_TOOL_SCHEMA]
    return tools, schemas


def build_worker_toolkit(task_type="coding", **kwargs):
    normalized = normalize_task_type(task_type)
    if normalized == "slide":
        return build_slide_worker_toolkit(**kwargs)
    return build_code_worker_toolkit(**kwargs)


def build_slide_worker_toolkit(
    *,
    api_key,
    model,
    run_log_path=None,
    turn_id=None,
    trace_sink=None,
    phase_sink=None,
):
    tools = {
        **SLIDE_WORKER_TOOLS,
        "agent_explorer": build_agent_explorer_tool(
            api_key=api_key,
            model=model,
            run_log_path=run_log_path,
            turn_id=turn_id,
            trace_sink=trace_sink,
            phase_sink=phase_sink,
        ),
    }
    schemas = list(SLIDE_WORKER_TOOL_SCHEMAS) + [AGENT_EXPLORER_TOOL_SCHEMA]
    return tools, schemas


def build_reviewer_toolkit(
    *,
    task_type="coding",
    api_key,
    model,
    run_log_path=None,
    turn_id=None,
    trace_sink=None,
    phase_sink=None,
):
    normalized = normalize_task_type(task_type)
    base_tools = SLIDE_REVIEWER_TOOLS if normalized == "slide" else REVIEWER_TOOLS
    base_schemas = SLIDE_REVIEWER_TOOL_SCHEMAS if normalized == "slide" else REVIEWER_TOOL_SCHEMAS
    tools = {
        **base_tools,
        "agent_explorer": build_agent_explorer_tool(
            api_key=api_key,
            model=model,
            run_log_path=run_log_path,
            turn_id=turn_id,
            trace_sink=trace_sink,
            phase_sink=phase_sink,
        ),
    }
    schemas = list(base_schemas) + [AGENT_EXPLORER_TOOL_SCHEMA]
    return tools, schemas


def new_worker_session(
    api_key,
    model,
    task_type="coding",
    trace_sink=None,
    approval_callback=None,
    run_log_path=None,
    turn_id=None,
    write_guard=None,
    phase_sink=None,
):
    tools, schemas = build_worker_toolkit(
        task_type=task_type,
        api_key=api_key,
        model=model,
        run_log_path=run_log_path,
        turn_id=turn_id,
        trace_sink=trace_sink,
        phase_sink=phase_sink,
    )
    return WorkerSession(
        api_key,
        model,
        schemas,
        tools,
        task_type=task_type,
        trace_sink=trace_sink,
        approval_callback=approval_callback,
        run_log_path=run_log_path,
        turn_id=turn_id,
        write_guard=write_guard,
    )


def new_reviewer_session(
    api_key,
    model,
    task_type="coding",
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    phase_sink=None,
):
    tools, schemas = build_reviewer_toolkit(
        task_type=task_type,
        api_key=api_key,
        model=model,
        run_log_path=run_log_path,
        turn_id=turn_id,
        trace_sink=trace_sink,
        phase_sink=phase_sink,
    )
    return ReviewerSession(
        api_key,
        model,
        schemas,
        tools,
        task_type=task_type,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )


def run_worker(
    api_key,
    model,
    task,
    context="",
    feedback="",
    task_type="coding",
    trace_sink=None,
    approval_callback=None,
    run_log_path=None,
    turn_id=None,
    session=None,
    user_prompt=None,
):
    if session is None:
        session = new_worker_session(
            api_key,
            model,
            task_type=task_type,
            trace_sink=trace_sink,
            approval_callback=approval_callback,
            run_log_path=run_log_path,
            turn_id=turn_id,
        )
    prompt = user_prompt if user_prompt is not None else worker_user_prompt(task, context, feedback)
    return session.send(prompt, assigned_task=task)


def run_reviewer(api_key, model, task, context="", trace_sink=None, run_log_path=None, turn_id=None, session=None):
    if session is None:
        session = new_reviewer_session(
            api_key, model, trace_sink=trace_sink, run_log_path=run_log_path, turn_id=turn_id
        )
    return session.send(reviewer_user_prompt(task, context), assigned_task=task)


class WorkerSession:
    def __init__(
        self,
        api_key,
        model,
        tool_schemas,
        tools,
        *,
        task_type="coding",
        trace_sink=None,
        approval_callback=None,
        run_log_path=None,
        turn_id=None,
        write_guard=None,
    ):
        self.api_key = api_key
        self.model = model
        self.tool_schemas = tool_schemas
        self.tools = tools
        self.task_type = normalize_task_type(task_type)
        self.label = "Worker"
        self.trace_sink = trace_sink
        self.approval_callback = approval_callback
        self.run_log_path = run_log_path
        self.turn_id = turn_id
        self.write_guard = write_guard
        self._worker_system_prompt = worker_system_prompt(self.task_type)
        self.messages, self.context, self.worklog_id = init_agent_context(
            system_prompt=self._worker_system_prompt,
            run_log_path=run_log_path,
            label=self.label,
        )
        self.tool_history = []
        self.step = 0
        self.assigned_task: str | None = None
        self._send_start_time: float | None = None

    def _apply_assigned_task(self, assigned_task=None) -> None:
        if assigned_task and str(assigned_task).strip():
            self.assigned_task = str(assigned_task).strip()
            self.context.stack.set_pinned_assigned_task(self.assigned_task)
        elif self.assigned_task:
            self.context.stack.set_pinned_assigned_task(self.assigned_task)

    def begin_send(self, user_prompt, *, assigned_task=None) -> None:
        """Start a worker phase: pin task and optional turn line."""
        self._apply_assigned_task(assigned_task)
        prompt = (user_prompt or "").strip()
        if prompt:
            self.context.begin_turn(prompt)
            write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, prompt)
        else:
            self.context.sync()
        self._send_start_time = now()

    def run_one_step(self, loop_budget: WorkerReviewerLoopBudget | None = None) -> tuple[StepOutcome, str | None]:
        """Run one model step (one tool round or final text)."""
        if loop_budget is not None:
            if loop_budget.exhausted():
                return StepOutcome.EXHAUSTED, None
            loop_budget.consume_step()
            time_start = loop_budget.start_time
            time_limit = loop_budget.max_seconds
            step_cap = loop_budget.max_steps
            step_count = loop_budget.used_steps
        else:
            if self.step >= MAX_WORKER_STEPS:
                return StepOutcome.EXHAUSTED, None
            time_start = self._send_start_time or now()
            time_limit = MAX_WORKER_SECONDS

        if over_time_budget(time_start, time_limit):
            return StepOutcome.TIMEOUT, None

        control.checkpoint()
        budget = prepare_agent_messages(
            self.messages,
            self.model,
            base_system_prompt=self._worker_system_prompt,
        )
        if over_context_budget(self.messages, budget):
            return StepOutcome.CONTEXT, None

        response = control.run_interruptible(
            lambda: create_model_response(
                self.api_key,
                self.model,
                self.messages,
                tool_schemas=self.tool_schemas,
                reasoning={"summary": "auto"},
                label=self.label,
                stream_sink=self.trace_sink,
            )
        )
        output = response.get("output", [])
        tool_calls = [item for item in output if item.get("type") == "function_call"]
        if not tool_calls:
            print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
            if output:
                finish_step(self.context, list(output), self, budget)
            self._send_start_time = None
            return StepOutcome.FINAL, extract_output_text(output)

        print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
        write_worklog_step(self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, output)
        step_items = list(output)
        for call in tool_calls:
            tool_output = run_worker_tool_call(
                call,
                self.tools,
                approval_callback=self.approval_callback,
                write_guard=self.write_guard,
                run_log_path=self.run_log_path,
            )
            self.tool_history.append({"call": call, "output": tool_output})
            step_items.append(tool_output)
            write_worklog_observation(
                self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, tool_output
            )
        self.step += 1
        finish_step(self.context, step_items, self, budget)
        return StepOutcome.TOOL, None

    def send(self, user_prompt, *, assigned_task=None, loop_budget=None):
        self.begin_send(user_prompt, assigned_task=assigned_task)
        while True:
            outcome, text = self.run_one_step(loop_budget)
            if outcome == StepOutcome.FINAL:
                return self._finish(text or "")
            if outcome == StepOutcome.TIMEOUT:
                return self._finish(stopped_report("ran out of time", self.tool_history))
            if outcome == StepOutcome.CONTEXT:
                return self._finish(stopped_report("exceeded the context budget", self.tool_history))
            if outcome == StepOutcome.EXHAUSTED:
                return self._finish(max_steps_report(self.tool_history))

    def _finish(self, report):
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        return report


class ReviewerSession:
    def __init__(
        self,
        api_key,
        model,
        tool_schemas,
        tools,
        *,
        task_type="coding",
        trace_sink=None,
        run_log_path=None,
        turn_id=None,
    ):
        self.api_key = api_key
        self.model = model
        self.tool_schemas = tool_schemas
        self.tools = tools
        self.task_type = normalize_task_type(task_type)
        self.label = "Reviewer"
        self.trace_sink = trace_sink
        self.run_log_path = run_log_path
        self.turn_id = turn_id
        self._reviewer_system_prompt = reviewer_system_prompt(self.task_type)
        self.messages, self.context, self.worklog_id = init_agent_context(
            system_prompt=self._reviewer_system_prompt,
            run_log_path=run_log_path,
            label=self.label,
        )
        self.step = 0
        self.assigned_task: str | None = None
        self._send_start_time: float | None = None

    def _apply_assigned_task(self, assigned_task=None) -> None:
        if assigned_task and str(assigned_task).strip():
            self.assigned_task = str(assigned_task).strip()
            self.context.stack.set_pinned_assigned_task(self.assigned_task)
        elif self.assigned_task:
            self.context.stack.set_pinned_assigned_task(self.assigned_task)

    def begin_send(self, user_prompt, *, assigned_task=None) -> None:
        self._apply_assigned_task(assigned_task)
        prompt = (user_prompt or "").strip()
        if prompt:
            self.context.begin_turn(prompt)
            write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, prompt)
        else:
            self.context.sync()
        self._send_start_time = now()

    def run_one_step(self, loop_budget: WorkerReviewerLoopBudget | None = None) -> tuple[StepOutcome, str | None]:
        if loop_budget is not None:
            if loop_budget.exhausted():
                return StepOutcome.EXHAUSTED, None
            loop_budget.consume_step()
            time_start = loop_budget.start_time
            time_limit = loop_budget.max_seconds
        else:
            if self.step >= MAX_REVIEWER_STEPS:
                return StepOutcome.EXHAUSTED, None
            time_start = self._send_start_time or now()
            time_limit = MAX_REVIEWER_SECONDS

        if over_time_budget(time_start, time_limit):
            return StepOutcome.TIMEOUT, None

        control.checkpoint()
        budget = prepare_agent_messages(
            self.messages,
            self.model,
            base_system_prompt=self._reviewer_system_prompt,
        )
        if over_context_budget(self.messages, budget):
            return StepOutcome.CONTEXT, None

        response = control.run_interruptible(
            lambda: create_model_response(
                self.api_key,
                self.model,
                self.messages,
                tool_schemas=self.tool_schemas,
                reasoning={"summary": "auto"},
                label=self.label,
                stream_sink=self.trace_sink,
            )
        )
        output = response.get("output", [])
        tool_calls = [item for item in output if item.get("type") == "function_call"]
        if not tool_calls:
            print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
            if output:
                finish_step(self.context, list(output), self, budget)
            self._send_start_time = None
            return StepOutcome.FINAL, extract_output_text(output)

        print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
        write_worklog_step(self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, output)
        step_items = list(output)
        for call in tool_calls:
            tool_output = self._run_tool(call)
            step_items.append(tool_output)
            write_worklog_observation(
                self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, tool_output
            )
        self.step += 1
        finish_step(self.context, step_items, self, budget)
        return StepOutcome.TOOL, None

    def send(self, user_prompt, *, assigned_task=None, loop_budget=None):
        self.begin_send(user_prompt, assigned_task=assigned_task)
        while True:
            outcome, text = self.run_one_step(loop_budget)
            if outcome == StepOutcome.FINAL:
                return self._finish(text or "")
            if outcome == StepOutcome.TIMEOUT:
                return self._finish(f"{self.label} stopped: out of time.")
            if outcome == StepOutcome.CONTEXT:
                return self._finish(f"{self.label} stopped: context budget exceeded.")
            if outcome == StepOutcome.EXHAUSTED:
                return self._finish(f"{self.label} stopped: max steps.")

    def _run_tool(self, call):
        name = call.get("name")
        call_id = call.get("call_id")
        try:
            arguments = without_purpose(json.loads(call.get("arguments") or "{}"))
            if name not in self.tools:
                raise ValueError(f"Unknown Reviewer tool: {name}")
            output = self.tools[name](**arguments)
        except Exception as error:
            output = f"Tool error: {error}"
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    def _finish(self, report):
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        return report


def run_worker_tool_call(call, tools, approval_callback=None, write_guard=None, run_log_path=None):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = without_purpose(json.loads(call.get("arguments") or "{}"))
        if name not in tools:
            raise ValueError(f"Unknown Worker tool: {name}")
        if name == "read_plan" and run_log_path is not None:
            arguments["run_log_path"] = run_log_path
        if write_guard is not None and name in WORKER_WRITE_TOOLS:
            guard_error = write_guard(name, arguments)
            if guard_error:
                raise PermissionError(guard_error)
        if name in WORKER_WRITE_TOOLS and not approve_worker_tool_write(name, arguments, approval_callback):
            raise PermissionError(f"{name} was not approved")
        output = tools[name](**arguments)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def approve_worker_tool_write(name, arguments, approval_callback=None):
    if approval_callback is not None:
        return approval_callback("Worker", name, arguments)
    return approve_worker_write_tool(name, arguments)


def approve_worker_write_tool(name, arguments):
    if not sys.stdin.isatty():
        return False

    print(f"\nApprove worker write tool: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    answer = input("Allow worker to run this write tool? [y/N] ")
    if answer.strip().lower() in {"y", "yes"}:
        return True
    raise control.TurnAborted(f"{name} was denied.")


def max_steps_report(tool_history):
    return stopped_report("reached the maximum specialist tool-call steps", tool_history)


def stopped_report(reason, tool_history):
    header = f"WORKER_STATUS: IN_PROGRESS\nSummary: Worker stopped because it {reason}."
    if not tool_history:
        return header

    lines = [header, "", "Recent specialist tool activity:"]
    for item in tool_history[-8:]:
        call = item["call"]
        tool_output = item["output"]
        lines.append(format_tool_activity(call, tool_output))
    return "\n".join(lines)


def format_tool_activity(call, tool_output):
    name = call.get("name", "unknown")
    arguments = call.get("arguments") or "{}"
    output = compact_tool_output(tool_output.get("output", ""))
    return f"- {name}({arguments}) -> {output}"


def compact_tool_output(output, max_chars=500):
    compact = " ".join(str(output).split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + "..."


# --- Worker↔Reviewer loop and agent_worker tool ---


def build_task_context(messages, target, project=""):
    lines = []
    prior = recent_chat_turns(messages)
    if prior:
        transcript = "\n".join(
            f"{'User' if turn['role'] == 'user' else 'Assistant'}: {turn['content']}"
            for turn in prior[-8:]
        )
        lines.append(f"Conversation so far:\n{transcript}")
    lines.append(f"Latest user request:\n{target}")
    if project and project.strip() != target.strip():
        lines.append(f"Project focus:\n{project}")
    return "\n\n".join(lines)


def _run_git(*args, cwd=None):
    cwd = cwd or WORKSPACE_ROOT
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _is_git_repo(cwd=None):
    cwd = cwd or WORKSPACE_ROOT
    return (Path(cwd) / ".git").exists()


def snapshot_head(cwd=None):
    if not _is_git_repo(cwd):
        return None
    result = _run_git("rev-parse", "HEAD", cwd=cwd)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def commit_task(label, task, cwd=None):
    if not _is_git_repo(cwd):
        return None
    _run_git("add", "-A", cwd=cwd)
    status = _run_git("diff", "--cached", "--quiet", cwd=cwd)
    if status.returncode == 0:
        return snapshot_head(cwd)
    message = f"{label}: {task[:72]}"
    result = _run_git("commit", "-m", message, cwd=cwd)
    if result.returncode != 0:
        return None
    return snapshot_head(cwd)


def revert_snapshot(commit, cwd=None):
    if not commit or not _is_git_repo(cwd):
        return False
    reset = _run_git("reset", "--hard", commit, cwd=cwd)
    if reset.returncode != 0:
        return False
    clean = _run_git(
        "clean",
        "-fd",
        "-e",
        "agent-state",
        "-e",
        ".langbridge",
        cwd=cwd,
    )
    return clean.returncode == 0


def git_diff_since(snapshot: str | None, cwd=None) -> str:
    root = _git_cwd(cwd)
    if not snapshot:
        result = _run_git("diff", "--no-color", cwd=root)
        return result.stdout or ""
    result = _run_git("diff", "--no-color", snapshot, cwd=root)
    return result.stdout or ""


def _git_cwd(cwd=None) -> Path:
    return Path(cwd or WORKSPACE_ROOT).resolve()


def _locked_approval(approval_callback):
    if approval_callback is None:
        return None

    def approve(role, tool_name, arguments):
        with _APPROVAL_LOCK:
            return approval_callback(role, tool_name, arguments)

    return approve


def reviewer_context(context, worker_report, diff: str, *, task_type="coding") -> str:
    parts = []
    if context:
        parts.append(context)
    parts.append("Worker summary:\n" + worker_report)
    if normalize_task_type(task_type) == "coding":
        if diff.strip():
            parts.append("Git diff:\n" + diff[:16000])
        else:
            parts.append("Git diff: (empty)")
    else:
        parts.append(
            "Slide task: inspect deliverable files (e.g. .pptx) with read_file/glob — "
            "no git diff for this task type."
        )
    return "\n\n".join(parts)


def _loop_stop_report(outcome: StepOutcome, *, worker_report: str, reviewer_report: str) -> str:
    if outcome == StepOutcome.TIMEOUT:
        return "Worker/reviewer loop timed out."
    if outcome == StepOutcome.CONTEXT:
        return "Worker/reviewer loop exceeded the context budget."
    return reviewer_report or worker_report or "Worker/reviewer loop exhausted step budget."


def run_worker_reviewer_loop(
    api_key,
    model,
    task,
    context="",
    task_type="coding",
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    approval_callback=None,
    phase_sink=None,
    cwd=None,
) -> tuple[bool, str]:
    """One loop from LangBridge: worker until ready → reviewer → repeat; shared step budget."""
    normalized = normalize_task_type(task_type)
    use_git = normalized == "coding"
    git_root = _git_cwd(cwd)
    snapshot = snapshot_head(git_root) if use_git else None
    locked_approval = _locked_approval(approval_callback)
    worker = new_worker_session(
        api_key,
        model,
        task_type=normalized,
        trace_sink=trace_sink,
        approval_callback=locked_approval,
        run_log_path=run_log_path,
        turn_id=turn_id,
        phase_sink=phase_sink,
    )
    reviewer = new_reviewer_session(
        api_key,
        model,
        task_type=normalized,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        phase_sink=phase_sink,
    )
    loop_budget = WorkerReviewerLoopBudget(max_steps=MAX_WORKER_REVIEWER_STEPS)
    phase = "worker"
    feedback = ""
    worker_report = ""
    reviewer_report = ""
    worker_phase_open = False
    reviewer_phase_open = False
    diff = ""

    while loop_budget.steps_left() > 0 and not loop_budget.timed_out():
        if phase == "worker":
            if not worker_phase_open:
                worker.begin_send(
                    worker_user_prompt(task, context if not feedback else "", feedback),
                    assigned_task=task,
                )
                worker_phase_open = True

            outcome, text = worker.run_one_step(loop_budget)
            if outcome in {StepOutcome.EXHAUSTED, StepOutcome.TIMEOUT, StepOutcome.CONTEXT}:
                if use_git:
                    revert_snapshot(snapshot, git_root)
                append_event(
                    run_log_path,
                    {"event": "loop_stop", "phase": "worker", "outcome": outcome.value},
                )
                return False, _loop_stop_report(outcome, worker_report=worker_report, reviewer_report=reviewer_report)

            if outcome == StepOutcome.TOOL:
                continue

            worker_report = text or ""
            worker_phase_open = False
            append_event(
                run_log_path,
                {
                    "event": "worker_turn",
                    "steps_used": loop_budget.used_steps,
                    "report": worker_report,
                    "feedback_in": feedback,
                },
            )
            if not worker_ready_for_review(worker_report):
                if use_git:
                    revert_snapshot(snapshot, git_root)
                return False, worker_report

            diff = git_diff_since(snapshot, git_root) if use_git else ""
            phase = "reviewer"
            append_event(
                run_log_path,
                {"event": "handoff_to_reviewer", "steps_used": loop_budget.used_steps, "diff": diff[:12000]},
            )
            continue

        if phase == "reviewer":
            if not reviewer_phase_open:
                emit_phase(phase_sink, "reviewing")
                diff = git_diff_since(snapshot, git_root) if use_git else ""
                reviewer.begin_send(
                    reviewer_user_prompt(task, reviewer_context(context, worker_report, diff, task_type=normalized)),
                    assigned_task=task,
                )
                reviewer_phase_open = True

            outcome, text = reviewer.run_one_step(loop_budget)
            if outcome in {StepOutcome.EXHAUSTED, StepOutcome.TIMEOUT, StepOutcome.CONTEXT}:
                if use_git:
                    revert_snapshot(snapshot, git_root)
                append_event(
                    run_log_path,
                    {"event": "loop_stop", "phase": "reviewer", "outcome": outcome.value},
                )
                return False, _loop_stop_report(outcome, worker_report=worker_report, reviewer_report=reviewer_report)

            if outcome == StepOutcome.TOOL:
                continue

            reviewer_report = text or ""
            reviewer_phase_open = False
            append_event(
                run_log_path,
                {
                    "event": "reviewer_turn",
                    "steps_used": loop_budget.used_steps,
                    "report": reviewer_report,
                    "diff": diff[:12000],
                },
            )
            if reviewer_review_passed(reviewer_report):
                if use_git:
                    commit_task("worker", task, git_root)
                append_event(
                    run_log_path,
                    {"event": "approved", "steps_used": loop_budget.used_steps},
                )
                return True, reviewer_report

            feedback = reviewer_report
            phase = "worker"
            append_event(
                run_log_path,
                {"event": "handoff_to_worker", "steps_used": loop_budget.used_steps, "comment": feedback[:8000]},
            )

    if use_git:
        revert_snapshot(snapshot, git_root)
    append_event(run_log_path, {"event": "max_steps", "steps_used": loop_budget.used_steps})
    return False, reviewer_report or worker_report


def _next_worktree_index(run_log_path) -> int:
    key = str(Path(run_log_path).resolve()) if run_log_path else "default"
    with _WORKTREE_INDEX_LOCK:
        index = _worktree_index_by_session.get(key, 0) + 1
        _worktree_index_by_session[key] = index
        return index


def _parallel_worktree_context(task: str, worktree_path: Path) -> str:
    scope = parallel_prompt_paths(task)
    lines = [
        "You are working in an isolated git worktree for this task only.",
        f"Worktree path: {worktree_path}",
        "Do not modify files outside this worktree.",
    ]
    if scope:
        lines.append(f"Scope (stay within): {scope}")
    return "\n".join(lines)


def _run_worker_in_worktree(
    *,
    api_key,
    model,
    task,
    context,
    task_type,
    worktree_info,
    trace_sink,
    phase_sink,
    run_log_path,
    turn_id,
    approval_callback,
) -> tuple[bool, str]:
    task_text = clean_task_text(task)
    scoped_context = "\n\n".join(
        part
        for part in (context(task_text), _parallel_worktree_context(task, worktree_info.path))
        if part
    )
    with workspace_scope(worktree_info.path):
        return run_worker_reviewer_loop(
            api_key,
            model,
            task_text,
            scoped_context,
            task_type=task_type,
            trace_sink=trace_sink,
            phase_sink=phase_sink,
            run_log_path=run_log_path,
            turn_id=turn_id,
            approval_callback=approval_callback,
            cwd=worktree_info.path,
        )


def dispatch_worker(
    task,
    description="",
    *,
    task_type="coding",
    api_key,
    model,
    run_log_path,
    turn_id,
    target,
    context,
    trace_sink=None,
    phase_sink=None,
    approval_callback=None,
):
    normalized = normalize_task_type(task_type or read_task_type(run_log_path) or "coding")
    emit_phase(phase_sink, "working")

    use_worktree = (
        PARALLEL_AGENTS_ENABLED
        and normalized == "coding"
        and is_parallel_prompt(task)
        and not is_merge_task_prompt(task)
        and worktree_mod.is_git_repo()
    )
    worktree_info = None
    if use_worktree:
        try:
            worktree_info = worktree_mod.create_worktree(
                run_log_path,
                _next_worktree_index(run_log_path),
                task,
            )
        except RuntimeError as error:
            return f"[{description or 'worker'}] Worktree setup failed.\n\n{error}"

    if worktree_info is not None:
        passed, detail = _run_worker_in_worktree(
            api_key=api_key,
            model=model,
            task=task,
            context=context,
            task_type=normalized,
            worktree_info=worktree_info,
            trace_sink=trace_sink,
            phase_sink=phase_sink,
            run_log_path=run_log_path,
            turn_id=turn_id,
            approval_callback=approval_callback,
        )
        worktree_mod.record_branch(run_log_path, worktree_info, "ready" if passed else "failed")
        status = "completed" if passed else "stopped (review did not pass)"
        branch_note = f"\n\nWorktree branch: {worktree_info.branch}"
        if passed:
            branch_note += " (ready to merge)"
        return f"[{description or 'worker'}] Parallel worktree {status}.{branch_note}\n\n{detail[:4000]}{_todo_completion_suffix(task, passed, run_log_path)}"

    task_context = context(task)
    if is_merge_task_prompt(task) and run_log_path is not None:
        merge_ctx = _merge_task_context(run_log_path, task)
        task_context = "\n\n".join(part for part in (merge_ctx, task_context) if part)

    passed, detail = run_worker_reviewer_loop(
        api_key,
        model,
        task,
        task_context,
        task_type=normalized,
        trace_sink=trace_sink,
        phase_sink=phase_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        approval_callback=approval_callback,
    )
    if passed and is_merge_task_prompt(task) and run_log_path is not None:
        for branch in _branches_named_in_task(run_log_path, task):
            worktree_mod.mark_branch_status(run_log_path, branch, "merged")
    status = "completed" if passed else "stopped (review did not pass)"
    return f"[{description or 'worker'}] Single-task {status}.\n\n{detail[:4000]}{_todo_completion_suffix(task, passed, run_log_path)}"


def _todo_completion_suffix(task, passed, run_log_path):
    if not passed:
        return ""
    note = plan_tools.complete_subtask_after_review(task, run_log_path=run_log_path)
    return f"\n\n{note}" if note else ""


def build_agent_worker_tool(
    *,
    api_key,
    model,
    run_log_path,
    turn_id,
    messages,
    target,
    trace_sink=None,
    phase_sink=None,
    approval_callback=None,
    question_callback=None,
):
    def agent_worker(prompt, description="", task_type="coding"):
        canonical, error = resolve_single_worker_task(prompt, run_log_path)
        if error:
            return f"Tool error: {error}"
        task = canonical or ""
        if is_merge_task_prompt(task):
            merge_error = _validate_merge_prompt_single_branch(task, run_log_path)
            if merge_error:
                return f"Tool error: {merge_error}"
        return dispatch_worker(
            task,
            description,
            task_type=task_type,
            api_key=api_key,
            model=model,
            run_log_path=run_log_path,
            turn_id=turn_id,
            target=target,
            context=lambda project="": build_task_context(messages, target, project),
            trace_sink=trace_sink,
            phase_sink=phase_sink,
            approval_callback=approval_callback,
        )

    return agent_worker


def run_worker_component(
    api_key,
    model,
    arguments,
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    approval_callback=None,
):
    task = arguments.get("task", "")
    context = arguments.get("context", "")
    passed, detail = run_worker_reviewer_loop(
        api_key,
        model,
        task,
        context,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        approval_callback=approval_callback,
    )
    status = "OK" if passed else "NEEDS_WORK"
    return f"{detail}\n\nWORKFLOW_REVIEW_STATUS: {status}"
