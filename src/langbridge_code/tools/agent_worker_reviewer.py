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
from langbridge_code.agents.common.limits import now, over_time_budget
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
from langbridge_code.context.common.budget import messages_with_budget_notice, prepare_agent_messages
from langbridge_code.tools.approval import approval_reason
from langbridge_code.context.message import recent_chat_turns
from langbridge_code.context.agent_context import finish_step, init_agent_context
from langbridge_code.context.foreground import ForegroundTracker
from langbridge_code.settings import (
    MAX_WORKER_REVIEWER_SECONDS,
    MAX_WORKER_REVIEWER_STEPS,
    MAX_REVIEWER_SECONDS,
    MAX_REVIEWER_STEPS,
    MAX_WORKER_SECONDS,
    MAX_WORKER_STEPS,
    WORKSPACE_ROOT,
    model_for_agent,
)
from langbridge_code.agents.common.todo_list import (
    clean_task_text,
)
from langbridge_code.tools import (
    FILE_READ_TOOL_NAMES,
    FILE_WRITE_TOOL_NAMES,
    SHELL_TOOL_NAMES,
    execution,
    filesystem,
    skills,
)
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER, without_purpose
from langbridge_code.tools.common.runtime import managed_binary
from langbridge_code.skills import (
    ensure_skill_index_block,
    normalize_task_type,
    reviewer_skill_catalog,
    worker_skill_catalog,
)
from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.common.task_progress import TaskProgress
from langbridge_code.agents.common.workspace import workspace_scope
from langbridge_code.tools.note_progress import TASK_NOTE_PROGRESS_TOOL_SCHEMA
from langbridge_code.tools.memory_writer import MEMORY_WRITER_TOOL_SCHEMA
from langbridge_code.training.optimizer_trace import append_event

# --- Worker toolkits by task type ---

CODE_WORKER_TOOL_NAMES = (
    FILE_READ_TOOL_NAMES
    | FILE_WRITE_TOOL_NAMES
    | SHELL_TOOL_NAMES
    | {"read_skill"}
)
CODE_WORKER_TOOL_SCHEMAS = [
    schema
    for schema in (
        filesystem.TOOL_SCHEMAS
        + execution.TOOL_SCHEMAS
        + skills.TOOL_SCHEMAS
    )
    if schema["name"] in CODE_WORKER_TOOL_NAMES
]
CODE_WORKER_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS
        | execution.TOOLS
        | skills.TOOLS
    ).items()
    if name in CODE_WORKER_TOOL_NAMES
}
WORKER_WRITE_TOOLS = FILE_WRITE_TOOL_NAMES

# --- Reviewer specialist tools ---

REVIEWER_TOOL_NAMES = FILE_READ_TOOL_NAMES | SHELL_TOOL_NAMES | {"read_skill"}
REVIEWER_TOOL_SCHEMAS = [
    schema
    for schema in (
        filesystem.TOOL_SCHEMAS
        + execution.TOOL_SCHEMAS
        + skills.TOOL_SCHEMAS
    )
    if schema["name"] in REVIEWER_TOOL_NAMES
]
REVIEWER_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS | execution.TOOLS | skills.TOOLS
    ).items()
    if name in REVIEWER_TOOL_NAMES
}

_APPROVAL_LOCK = threading.Lock()

def is_merge_task_prompt(task: str) -> bool:
    cleaned = clean_task_text(task).lower()
    # Integration verification todos ("Verify merged ...") are worker tasks,
    # not merges, even when they mention merged branches.
    if cleaned.startswith("verify"):
        return False
    if "git merge" in cleaned:
        return True
    if "merge" in cleaned and "branch" in cleaned:
        return True
    if "resolve" in cleaned and "conflict" in cleaned:
        return True
    return bool(re.search(r"\blb/", task or "", re.IGNORECASE) and "merge" in cleaned)


AGENT_WORKER_TOOL_SCHEMA = {
    "type": "function",
    "name": "agent_worker",
    "description": (
        "Launch the worker-reviewer subagent for exactly one todo subtask. "
        "A new task starts without your chat context and never reads your plan file: the "
        "task_contract must be a word-for-word copy of that task's complete "
        "markdown block in todo_list.md. Put exploration findings — exact file "
        "paths, key functions/classes with line ranges, relevant snippets, and "
        "how the pieces connect — in supplemental_context, without changing the "
        "contract. "
        "One subtask per call — do not paste the entire plan or bundle todos. "
        "Every new coding task — single or batched — runs in its own isolated git "
        "worktree branched from HEAD, and the result reports its feature branch. "
        "Re-dispatching the exact same contract with the same task_name resumes "
        "that task's failed worktree, progress note, and trace tail. "
        "Merge each ready branch yourself with the merge_branch tool (never via "
        "agent_worker) before dispatching todos that depend on it. When several "
        "todos in todo_list.md are independent and unblocked, you may call "
        "agent_worker several times in one turn. "
        "Do not dispatch a todo whose prerequisites are still unchecked. "
        "The worker never touches todo_list.md — you own every task's status "
        "(unassigned, dispatched/in progress, finished): on reviewer PASS, mark "
        "the matching line `[x]` yourself with Edit. "
        "On stop before approval, partial work is preserved on the task's worktree "
        "branch (normal non-PASS returns are committed; hard Stop leaves completed "
        "edits in place). Leave it unmerged and re-dispatch the same "
        "contract/task_name with the previous return in supplemental_context. "
        "Only merge a completed/PASS branch. If the contract changes, use a fresh "
        "task_name. "
        "Returns a final summary only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": PURPOSE_PARAMETER,
            "task_contract": {
                "type": "string",
                "description": (
                    "Word-for-word copy of exactly one complete task block from "
                    "todo_list.md, including title, Objective, Detailed requirements, "
                    "Acceptance spec, Deliverables, Verify, Out of scope, and deps. "
                    "Do not summarize, rewrite, or omit any part."
                ),
            },
            "supplemental_context": {
                "type": "string",
                "description": (
                    "Additional facts discovered after the contract was written: "
                    "exact paths, line ranges, relevant snippets, and how components "
                    "connect. When resuming the same task_name, include the previous "
                    "agent_worker return so the worker knows why it stopped and what "
                    "review feedback remains. Must not override or reinterpret the "
                    "task contract."
                ),
            },
            "description": {
                "type": "string",
                "description": "Short 3-5 word title for logging.",
            },
            "task_name": {
                "type": "string",
                "description": (
                    "Stable name for this todo/task (e.g. 'task-3-game-state'). "
                    "Names the task's progress note file: the worker's notes are "
                    "saved under it and shown to the next worker dispatched with "
                    "the SAME task_name — reuse the exact name when re-dispatching "
                    "or continuing a task so it resumes from those notes."
                ),
            },
        },
        "required": ["purpose", "task_contract", "description", "task_name"],
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


def _last_status_line(report: str, prefix: str) -> str:
    """Last line starting with `prefix`, markdown emphasis stripped, spaces squashed.

    Prompts ask for the marker as the LAST line of the report, matching how
    models naturally write (reasoning first, conclusion last). Taking the final
    occurrence also stays lenient: an early strict requirement (line 1 only)
    turned a real PASS with a prose preamble into "needs work" feedback and
    ping-ponged the loop forever. Reports from older checkpoints that still put
    the marker on line 1 parse fine as long as it appears once.
    """
    match = ""
    for line in (report or "").splitlines():
        cleaned = " ".join(line.strip().strip("*_`#").split()).lower()
        if cleaned.startswith(prefix):
            match = cleaned
    return match


def worker_ready_for_review(report):
    return _last_status_line(report, "worker_status:") == "worker_status: ready_for_review"


def worker_blocked(report):
    return _last_status_line(report, "worker_status:") == "worker_status: blocked"


def reviewer_review_passed(report):
    return _last_status_line(report, "review_verdict:") == "review_verdict: pass"


def build_code_worker_toolkit(
    *,
    api_key,
    model,
    run_log_path=None,
    turn_id=None,
    trace_sink=None,
    phase_sink=None,
):
    return dict(CODE_WORKER_TOOLS), list(CODE_WORKER_TOOL_SCHEMAS) + [MEMORY_WRITER_TOOL_SCHEMA]


def build_worker_toolkit(task_type="coding", **kwargs):
    return build_code_worker_toolkit(**kwargs)


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
    return dict(REVIEWER_TOOLS), list(REVIEWER_TOOL_SCHEMAS) + [MEMORY_WRITER_TOOL_SCHEMA]


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
    task_name="",
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
        task_name=task_name,
    )


def new_reviewer_session(
    api_key,
    model,
    task_type="coding",
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    phase_sink=None,
    task_name="",
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
        task_name=task_name,
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
    task_name="",
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
            task_name=task_name,
        )
    prompt = user_prompt if user_prompt is not None else worker_user_prompt(task, context, feedback)
    return session.send(prompt, assigned_task=task)


def run_reviewer(
    api_key,
    model,
    task,
    context="",
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    session=None,
    task_name="",
):
    if session is None:
        session = new_reviewer_session(
            api_key,
            model,
            trace_sink=trace_sink,
            run_log_path=run_log_path,
            turn_id=turn_id,
            task_name=task_name,
        )
    return session.send(reviewer_user_prompt(task, context), assigned_task=task)


class MemoryPhaseMixin:
    """Same memory triangle as the main agent: prefetch, mid-phase fork, end catch-up."""

    def _init_memory_phase_state(self) -> None:
        self._memory_writer_ran_this_send = False
        self._memory_hooks_ready = False
        self.tools["memory_writer"] = self._invoke_memory_writer

    def _refresh_memory_block(self, task: str = "") -> None:
        from langbridge_code.memory import prefetch_memory

        self.context.stack.set_memory_block(
            prefetch_memory(
                self.api_key,
                self.model,
                task or getattr(self, "assigned_task", None) or "",
            )
        )

    def _ensure_memory_hooks(self) -> None:
        if self._memory_hooks_ready:
            return
        previous = self.context.stack.on_compacted

        def on_compacted(compacted_stack):
            if previous is not None:
                try:
                    previous(compacted_stack)
                except Exception:
                    pass
            self._refresh_memory_block()

        self.context.stack.on_compacted = on_compacted
        self._memory_hooks_ready = True

    def _begin_memory_phase(self) -> None:
        self._memory_writer_ran_this_send = False
        self._refresh_memory_block(getattr(self, "assigned_task", None) or "")
        self._ensure_memory_hooks()

    def _invoke_memory_writer(self, **_kwargs):
        from langbridge_code.memory import run_memory_writer_agent

        report = run_memory_writer_agent(self.api_key, self.model, list(self.messages))
        self._memory_writer_ran_this_send = True
        return report

    def _schedule_memory_if_needed(self) -> None:
        from langbridge_code.memory import schedule_memory_writer

        if self._memory_writer_ran_this_send:
            return
        schedule_memory_writer(self.api_key, self.model, self.messages)
        self._memory_writer_ran_this_send = True


class WorkerSession(MemoryPhaseMixin):
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
        task_name="",
    ):
        self.api_key = api_key
        self.model = model
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
            task_name=task_name,
        )
        self.task_progress = TaskProgress(
            api_key,
            model,
            run_log_path,
            task_name,
            label=self.label,
            current_trace=self.context.agent_trace_path,
        )
        self.tools = dict(tools)
        self.tool_schemas = list(tool_schemas)
        self._init_memory_phase_state()
        if self.task_progress.enabled:
            self.tools["note_progress"] = self.task_progress.write_note
            self.tool_schemas.append(TASK_NOTE_PROGRESS_TOOL_SCHEMA)
            self.task_progress.attach(self.context.stack, self.messages)
        self.tool_history = []
        self.step = 0
        self.assigned_task: str | None = None
        self._send_start_time: float | None = None
        self._foreground: ForegroundTracker | None = None

    def _activate_foreground(self) -> None:
        if self._foreground is None:
            self._foreground = ForegroundTracker(self.label, self.messages, self.model)
        self._foreground.activate()

    def _publish_foreground(self) -> None:
        if self._foreground is not None:
            self._foreground.publish()

    def _deactivate_foreground(self) -> None:
        if self._foreground is not None:
            self._foreground.deactivate()
            self._foreground = None

    def _apply_assigned_task(self, assigned_task=None) -> None:
        if assigned_task and str(assigned_task).strip():
            self.assigned_task = str(assigned_task).strip()
            self.context.stack.set_pinned_assigned_task(self.assigned_task)
        elif self.assigned_task:
            self.context.stack.set_pinned_assigned_task(self.assigned_task)
        ensure_skill_index_block(
            self.context.stack,
            self.api_key,
            self.model,
            self.assigned_task or "",
            worker_skill_catalog(self.task_type),
            label=f"{self.label} skill prefetch",
        )

    def begin_send(self, user_prompt, *, assigned_task=None) -> None:
        """Start a worker phase: pin task and optional turn line."""
        self._apply_assigned_task(assigned_task)
        self._begin_memory_phase()
        prompt = (user_prompt or "").strip()
        if prompt:
            self.context.begin_turn(prompt)
            write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, prompt)
        else:
            self.context.sync()
        self._send_start_time = now()
        self._activate_foreground()

    def run_one_step(self, loop_budget: WorkerReviewerLoopBudget | None = None) -> tuple[StepOutcome, str | None]:
        """Run one model step (one tool round or final text)."""
        if loop_budget is not None:
            if loop_budget.exhausted():
                self._deactivate_foreground()
                return StepOutcome.EXHAUSTED, None
            loop_budget.consume_step()
            time_start = loop_budget.start_time
            time_limit = loop_budget.max_seconds
            step_cap = loop_budget.max_steps
            step_count = loop_budget.used_steps
        else:
            if self.step >= MAX_WORKER_STEPS:
                self._deactivate_foreground()
                return StepOutcome.EXHAUSTED, None
            time_start = self._send_start_time or now()
            time_limit = MAX_WORKER_SECONDS

        if over_time_budget(time_start, time_limit):
            self._deactivate_foreground()
            return StepOutcome.TIMEOUT, None

        control.checkpoint()
        self.context.compact_to_budget(api_key=self.api_key, model=self.model)
        budget = prepare_agent_messages(
            self.messages,
            self.model,
            base_system_prompt=self._worker_system_prompt,
        )
        self._publish_foreground()
        response = control.run_interruptible(
            lambda: create_model_response(
                self.api_key,
                self.model,
                messages_with_budget_notice(self.messages, self.model),
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
                self._publish_foreground()
            self._send_start_time = None
            self._deactivate_foreground()
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
        self.task_progress.maybe_remind(self.context)
        self._publish_foreground()
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
        self._deactivate_foreground()
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        self._schedule_memory_if_needed()
        return report


class ReviewerSession(MemoryPhaseMixin):
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
        task_name="",
    ):
        self.api_key = api_key
        self.model = model
        self.tool_schemas = list(tool_schemas)
        self.tools = dict(tools)
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
            task_name=task_name,
        )
        self.task_progress = TaskProgress(
            api_key,
            model,
            run_log_path,
            task_name,
            label=self.label,
            current_trace=self.context.agent_trace_path,
        )
        self._init_memory_phase_state()
        if self.task_progress.enabled:
            self.task_progress.attach(self.context.stack, self.messages)
        self.step = 0
        self.assigned_task: str | None = None
        self._send_start_time: float | None = None
        self._foreground: ForegroundTracker | None = None

    def _activate_foreground(self) -> None:
        if self._foreground is None:
            self._foreground = ForegroundTracker(self.label, self.messages, self.model)
        self._foreground.activate()

    def _publish_foreground(self) -> None:
        if self._foreground is not None:
            self._foreground.publish()

    def _deactivate_foreground(self) -> None:
        if self._foreground is not None:
            self._foreground.deactivate()
            self._foreground = None

    def _apply_assigned_task(self, assigned_task=None) -> None:
        if assigned_task and str(assigned_task).strip():
            self.assigned_task = str(assigned_task).strip()
            self.context.stack.set_pinned_assigned_task(self.assigned_task)
        elif self.assigned_task:
            self.context.stack.set_pinned_assigned_task(self.assigned_task)
        ensure_skill_index_block(
            self.context.stack,
            self.api_key,
            self.model,
            self.assigned_task or "",
            reviewer_skill_catalog(self.task_type),
            label=f"{self.label} skill prefetch",
        )

    def begin_send(self, user_prompt, *, assigned_task=None) -> None:
        self._apply_assigned_task(assigned_task)
        self._begin_memory_phase()
        prompt = (user_prompt or "").strip()
        if prompt:
            self.context.begin_turn(prompt)
            write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, prompt)
        else:
            self.context.sync()
        self._send_start_time = now()
        self._activate_foreground()

    def run_one_step(self, loop_budget: WorkerReviewerLoopBudget | None = None) -> tuple[StepOutcome, str | None]:
        if loop_budget is not None:
            if loop_budget.exhausted():
                self._deactivate_foreground()
                return StepOutcome.EXHAUSTED, None
            loop_budget.consume_step()
            time_start = loop_budget.start_time
            time_limit = loop_budget.max_seconds
        else:
            if self.step >= MAX_REVIEWER_STEPS:
                self._deactivate_foreground()
                return StepOutcome.EXHAUSTED, None
            time_start = self._send_start_time or now()
            time_limit = MAX_REVIEWER_SECONDS

        if over_time_budget(time_start, time_limit):
            self._deactivate_foreground()
            return StepOutcome.TIMEOUT, None

        control.checkpoint()
        self.context.compact_to_budget(api_key=self.api_key, model=self.model)
        budget = prepare_agent_messages(
            self.messages,
            self.model,
            base_system_prompt=self._reviewer_system_prompt,
        )
        self._publish_foreground()
        response = control.run_interruptible(
            lambda: create_model_response(
                self.api_key,
                self.model,
                messages_with_budget_notice(self.messages, self.model),
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
                self._publish_foreground()
            self._send_start_time = None
            self._deactivate_foreground()
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
        self._publish_foreground()
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
        self._deactivate_foreground()
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        self._schedule_memory_if_needed()
        return report


def run_worker_tool_call(call, tools, approval_callback=None, write_guard=None, run_log_path=None):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = without_purpose(json.loads(call.get("arguments") or "{}"))
        if name not in tools:
            raise ValueError(f"Unknown Worker tool: {name}")
        if write_guard is not None and name in WORKER_WRITE_TOOLS:
            guard_error = write_guard(name, arguments)
            if guard_error:
                raise PermissionError(guard_error)
        risk = approval_reason(name, arguments)
        if risk and not approve_worker_tool_write(name, arguments, approval_callback):
            raise PermissionError(f"{name} was not approved ({risk})")
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

    print(f"\nApprove high-risk worker tool: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    answer = input("Allow worker to run this high-risk tool? [y/N] ")
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
        [managed_binary("git"), *args],
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


def partial_work_note(snapshot, cwd=None) -> str:
    """Describe work left in the tree after a failed loop (nothing is reverted)."""
    root = _git_cwd(cwd)
    if not _is_git_repo(root):
        return ""
    parts = []
    diff_args = ["diff", "--stat"] + ([snapshot] if snapshot else [])
    stat = _run_git(*diff_args, cwd=root).stdout.strip()
    if stat:
        parts.append(stat)
    untracked = _run_git("ls-files", "--others", "--exclude-standard", cwd=root).stdout.strip()
    untracked_lines = [
        line
        for line in untracked.splitlines()
        if not line.startswith(("agent-state/", ".langbridge"))
    ]
    if untracked_lines:
        parts.append("Untracked files:\n" + "\n".join(untracked_lines))
    if not parts:
        return ""
    body = "\n".join(parts)
    return (
        "\n\nPartial work left in the working tree (not reverted):\n"
        + body[:2000]
        + "\nMain agent decides: re-dispatch agent_worker to continue from this state, "
        "or revise todo_list.md (Edit) into smaller steps accounting for these changes."
    )


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
    if diff.strip():
        parts.append("Git diff:\n" + diff[:16000])
    else:
        parts.append("Git diff: (empty)")
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
    task_name="",
    base_snapshot=None,
) -> tuple[bool, str]:
    """One loop from LangBridge: worker until ready → reviewer → repeat; shared step budget."""
    normalized = normalize_task_type(task_type)
    git_root = _git_cwd(cwd)
    snapshot = base_snapshot or snapshot_head(git_root)
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
        task_name=task_name,
    )
    reviewer = new_reviewer_session(
        api_key,
        model_for_agent("reviewer", model),
        task_type=normalized,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        phase_sink=phase_sink,
        task_name=task_name,
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
                worker._schedule_memory_if_needed()
                append_event(
                    run_log_path,
                    {"event": "loop_stop", "phase": "worker", "outcome": outcome.value},
                )
                report = _loop_stop_report(outcome, worker_report=worker_report, reviewer_report=reviewer_report)
                return False, report + (partial_work_note(snapshot, git_root))

            if outcome == StepOutcome.TOOL:
                continue

            worker_report = text or ""
            worker._schedule_memory_if_needed()
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
                return False, worker_report + (partial_work_note(snapshot, git_root))

            diff = git_diff_since(snapshot, git_root)
            phase = "reviewer"
            append_event(
                run_log_path,
                {"event": "handoff_to_reviewer", "steps_used": loop_budget.used_steps, "diff": diff[:12000]},
            )
            continue

        if phase == "reviewer":
            if not reviewer_phase_open:
                emit_phase(phase_sink, "reviewing")
                diff = git_diff_since(snapshot, git_root)
                reviewer.begin_send(
                    reviewer_user_prompt(task, reviewer_context(context, worker_report, diff, task_type=normalized)),
                    assigned_task=task,
                )
                reviewer_phase_open = True

            outcome, text = reviewer.run_one_step(loop_budget)
            if outcome in {StepOutcome.EXHAUSTED, StepOutcome.TIMEOUT, StepOutcome.CONTEXT}:
                reviewer._schedule_memory_if_needed()
                append_event(
                    run_log_path,
                    {"event": "loop_stop", "phase": "reviewer", "outcome": outcome.value},
                )
                report = _loop_stop_report(outcome, worker_report=worker_report, reviewer_report=reviewer_report)
                return False, report + (partial_work_note(snapshot, git_root))

            if outcome == StepOutcome.TOOL:
                continue

            reviewer_report = text or ""
            reviewer._schedule_memory_if_needed()
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

    append_event(run_log_path, {"event": "max_steps", "steps_used": loop_budget.used_steps})
    if worker_phase_open:
        worker._schedule_memory_if_needed()
    if reviewer_phase_open:
        reviewer._schedule_memory_if_needed()
    report = reviewer_report or worker_report
    return False, report + (partial_work_note(snapshot, git_root))


def _parallel_worktree_context(task: str, worktree_path: Path, *, resumed=False) -> str:
    lines = [
        "You are working in an isolated git worktree for this task only.",
        f"Worktree path: {worktree_path}",
        "Do not modify files outside this worktree.",
    ]
    if resumed:
        lines.extend(
            [
                "This is the SAME task resumed after an earlier incomplete dispatch.",
                "The worktree already contains its partial work. Inspect it "
                "before editing; continue from it rather than restarting.",
                "Your <progress> block also includes this task's earlier progress note "
                "and recoverable raw trace tail.",
            ]
        )
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
    task_name="",
    resumed=False,
) -> tuple[bool, str]:
    task_text = clean_task_text(task)
    scoped_context = "\n\n".join(
        part
        for part in (
            context(task_text),
            _parallel_worktree_context(task, worktree_info.path, resumed=resumed),
        )
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
            task_name=task_name,
            base_snapshot=worktree_info.base_commit,
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
    task_name="",
):
    normalized = normalize_task_type(task_type or "coding")
    emit_phase(phase_sink, "working")

    # Every coding task — including a single sequential one — runs in its own
    # worktree branched from HEAD. This keeps the worker's diff, commits, and
    # any reverts fully isolated from the main workspace: uncommitted main-agent
    # state (e.g. todo_list.md ticks) can neither pollute the worker's diff nor
    # be clobbered by the worker.
    use_worktree = (
        not is_merge_task_prompt(task)
        and worktree_mod.is_git_repo()
    )
    worktree_info = None
    resumed = False
    if use_worktree:
        worktree_info = worktree_mod.resumable_worktree(
            run_log_path,
            task_name=task_name,
            task_description=task,
        )
        resumed = worktree_info is not None
        try:
            if worktree_info is None:
                worktree_info = worktree_mod.create_worktree(
                    run_log_path,
                    task,
                    task_name=task_name,
                )
        except RuntimeError as error:
            return f"[{description or 'worker'}] Worktree setup failed.\n\n{error}"

    if worktree_info is not None:
        worktree_mod.record_branch(run_log_path, worktree_info, "working")
        try:
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
                task_name=task_name,
                resumed=resumed,
            )
        except control.StopRequested:
            # Hard stop must return immediately, so do not run a commit hook.
            # Keep the existing worktree (including uncommitted edits) and index
            # it for the same-task re-dispatch path.
            worktree_mod.record_branch(run_log_path, worktree_info, "failed")
            raise
        if not passed:
            # Preserve partial work on the task branch so it survives worktree
            # cleanup and can be merged later if the main agent wants it.
            commit_task("worker-partial", task, worktree_info.path)
        worktree_mod.record_branch(run_log_path, worktree_info, "ready" if passed else "failed")
        blocked = worker_blocked(detail)
        status = "completed" if passed else ("blocked" if blocked else "stopped before approval")
        branch_note = f"\n\nWorktree branch: {worktree_info.branch}"
        if passed:
            branch_note += " (ready to merge)"
        elif blocked:
            branch_note += (
                " (the task contract needs clarification; any partial work is "
                "committed on this branch)"
            )
        else:
            branch_note += (
                " (partial work is committed on this branch — do NOT merge it yet; "
                "re-dispatch the same task_contract with the same task_name and the "
                "previous return in supplemental_context to resume here)"
            )
        return f"[{description or 'worker'}] Worktree task {status}.{branch_note}\n\n{detail}{_todo_completion_suffix(passed)}"

    task_context = context(task)

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
        task_name=task_name,
    )
    status = "completed" if passed else (
        "blocked" if worker_blocked(detail) else "stopped before approval"
    )
    return f"[{description or 'worker'}] Single-task {status}.\n\n{detail}{_todo_completion_suffix(passed)}"


def _todo_completion_suffix(passed):
    if not passed:
        return ""
    return (
        "\n\nNext: if this task is a todo in todo_list.md, mark that line `[x]` "
        "yourself (Edit), then dispatch the next unblocked todos — or report "
        "to the user if everything is done."
    )


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
    def agent_worker(
        task_contract="",
        description="",
        task_type="coding",
        task_name="",
        supplemental_context="",
        prompt="",
    ):
        # `prompt` remains as a Python-call compatibility alias for older tests
        # and integrations; the public tool schema requires task_contract.
        task = (task_contract or prompt or "").strip()
        if not task:
            return "Tool error: task_contract must contain one complete todo task block."
        if is_merge_task_prompt(task):
            return (
                "Tool error: merge tasks are not delegated to agent_worker. "
                "Merge ready branches yourself with the merge_branch tool "
                "(one branch per call); resolve conflicts with Edit + git add + git commit."
            )
        return dispatch_worker(
            task,
            description,
            task_type=task_type,
            api_key=api_key,
            model=model,
            run_log_path=run_log_path,
            turn_id=turn_id,
            target=target,
            context=lambda project="": (supplemental_context or "").strip(),
            trace_sink=trace_sink,
            phase_sink=phase_sink,
            approval_callback=approval_callback,
            task_name=(task_name or "").strip(),
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
