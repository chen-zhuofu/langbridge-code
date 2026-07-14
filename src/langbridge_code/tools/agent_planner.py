"""Planner subagent loop and agent_planner tool implementation."""
import json

from langbridge_code.agents.common import control
from langbridge_code.agents.common.limits import now, over_time_budget
from langbridge_code.llm.client import create_model_response
from langbridge_code.llm.parse import extract_output_text, print_step_trace
from langbridge_code.tools.common.purpose import without_purpose
from langbridge_code.util.agent_worklog import (
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
    write_worklog_step,
)
from langbridge_code.context.common.budget import prepare_agent_messages
from langbridge_code.context.agent_context import finish_step, init_agent_context
from langbridge_code.context.foreground import ForegroundTracker
from langbridge_code.settings import (
    MAX_PLANNER_SECONDS,
    MAX_PLANNER_STEPS,
)
from langbridge_code.tools import (
    FILE_READ_TOOL_NAMES,
    GIT_READ_TOOL_NAMES,
    filesystem,
    git_tools as git_tools_mod,
    lsp,
    skills,
)
from langbridge_code.agents.common.phases import emit_phase
from langbridge_code.agents.system_prompt.planner import PLANNER_WORKFLOW_SUMMARY, planner_system_prompt
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER
from langbridge_code.agents.common.todo_list import (
    load_tasks,
    read_todo_list,
    unfinished_count,
    write_task_type_marker,
    write_todo_list,
)
from langbridge_code.tools.todo_list import extract_plan_markdown


def persist_task_type(run_log_path, task_type: str) -> None:
    content = read_todo_list(run_log_path) or ""
    write_todo_list(write_task_type_marker(content, task_type), run_log_path=run_log_path)


def _unfinished_tasks(run_log_path, *, limit: int | None = None):
    tasks = [task for task in load_tasks(run_log_path) if task.unfinished]
    if limit is None:
        return tasks
    return tasks[:limit]


def format_unfinished_todo_summary(run_log_path, *, limit: int = 5) -> str:
    tasks = _unfinished_tasks(run_log_path)
    if not tasks:
        return ""
    shown = tasks[:limit]
    lines = [f"- {task.description}" for task in shown]
    remaining = len(tasks) - len(shown)
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")
    return "\n".join(lines)


PLANNER_TOOL_NAMES = (
    FILE_READ_TOOL_NAMES
    | {"read_skill", "lsp"}
    | GIT_READ_TOOL_NAMES
)
PLANNER_TOOL_SCHEMAS = [
    schema
    for schema in (
        filesystem.TOOL_SCHEMAS
        + git_tools_mod.TOOL_SCHEMAS
        + lsp.TOOL_SCHEMAS
        + skills.TOOL_SCHEMAS
    )
    if schema["name"] in PLANNER_TOOL_NAMES
]
PLANNER_TOOLS = {
    **{name: filesystem.TOOLS[name] for name in FILE_READ_TOOL_NAMES},
    **{name: git_tools_mod.TOOLS[name] for name in GIT_READ_TOOL_NAMES},
    "lsp": lsp.TOOLS["lsp"],
    **skills.TOOLS,
}


AGENT_PLANNER_TOOL_SCHEMA = {
    "type": "function",
    "name": "agent_planner",
    "description": (
        "Offload plan research/drafting so repo exploration stays OUT of your "
        "main-agent context. You get ONE draft result back — not the planner's "
        "tool trace. Review that draft as if you wrote it; ask the user if "
        "ambiguous; then update_plan. Do not dispatch agent_worker until "
        "update_plan has committed. Blocked while an unfinished todo_list exists "
        "unless you called clear_plan first or set replace_existing_plan=true "
        "after the user confirmed replacing. When unfinished todos exist and the "
        "user starts a new multi-step task, read_plan then ask whether to "
        "continue / replace / /new before calling this."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": PURPOSE_PARAMETER,
            "prompt": {
                "type": "string",
                "description": "Full task description for the planner.",
            },
            "description": {
                "type": "string",
                "description": "Short 3-5 word title for logging.",
            },
            "replace_existing_plan": {
                "type": "boolean",
                "description": (
                    "Set true only after ask_user confirmed replacing the current "
                    "unfinished session todo_list. Omit or false otherwise."
                ),
            },
        },
        "required": ["purpose", "prompt", "description"],
        "additionalProperties": False,
    },
}


def planner_replace_blocked_message(run_log_path) -> str | None:
    remaining = unfinished_count(load_tasks(run_log_path))
    if remaining == 0:
        return None
    summary = format_unfinished_todo_summary(run_log_path)
    lines = [
        "Tool error: This session already has an unfinished todo_list that "
        f"agent_planner would overwrite ({remaining} item(s) remaining).",
        "Call read_plan, then ask_user how to proceed before planning again.",
        "If the user chooses to replace the plan, call clear_plan then "
        "agent_planner (or agent_planner with replace_existing_plan=true), review "
        "the draft, ask_user if unsure, then update_plan to commit.",
        "If they want to continue the old plan, use agent_worker.",
        "If they want the new task in a fresh session, tell them to run /new and "
        "repeat the request there.",
    ]
    if summary:
        lines.extend(["", "Unfinished items:", summary])
    return "\n".join(lines)


def run_planner(
    api_key,
    model,
    prompt: str,
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
) -> str:
    session = PlannerSession(
        api_key,
        model,
        planner_system_prompt(),
        PLANNER_TOOL_SCHEMAS,
        PLANNER_TOOLS,
        "Planner",
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )
    return session.send(prompt)


def initial_plan_prompt(user_task: str) -> str:
    return (
        f"{PLANNER_WORKFLOW_SUMMARY}\n"
        "Create an evidence-based plan for this user task.\n\n"
        "Before your final reply:\n"
        "1. Read user-named files and primary context FULLY (no limit/offset).\n"
        "2. grep/glob the repo; cite `path:line` for every discovery.\n"
        "3. If assumptions are unclear, put them in Open questions — do NOT ask the user.\n\n"
        "Your final reply must start with PLAN_TASK_TYPE, then a ```markdown fenced\n"
        "block with the FULL plan: Desired end state, Success criteria,\n"
        "Key discoveries, Out of scope, Current state, Design options (if non-trivial),\n"
        "Open questions, Todo list (each with <!-- depends: ... --> and <!-- verify: ... -->),\n"
        "Changes required (file:line + code snippets when known).\n"
        "You have no update_plan or ask_user — the main agent commits the plan.\n\n"
        "Todo lines must be:\n"
        "  - [ ] <description> <!-- depends: none|N,M --> <!-- verify: ... -->\n"
        "Decide coding vs slide — entire todo_list is one type only.\n"
        "For coding: build and verify working software; no design docs unless asked.\n"
        "For slide: build the deck deliverable (.pptx); verify content and structure.\n"
        "Split independent edits into separate todos; file/function-level steps are fine.\n"
        "No padding or duplicates.\n"
        "For 3+ coding implementation steps, end with <!-- integration --> verification.\n"
        "Ready todos (depends satisfied) are dispatched together — no parallel marker.\n\n"
        "After the fenced plan, add a brief ## Summary.\n\n"
        f"User task:\n{user_task}"
    )


def parse_plan_task_type(report: str) -> str | None:
    for line in (report or "").strip().splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("plan_task_type:"):
            value = stripped.split(":", 1)[1].strip()
            if value in {"coding", "slide", "presentation"}:
                return "slide" if value in {"slide", "presentation"} else "coding"
    return None


def refine_plan_prompt(
    failed_task: str,
    reason: str,
    todo: str,
    *,
    task_type: str = "coding",
) -> str:
    return (
        "The task below did not complete in the workflow outer loop. Replace ONLY "
        "that task in the todo_list with 2-4 smaller steps. Each line must be "
        "  - [ ] <description> <!-- depends: ... --> <!-- verify: <exact command> -->\n"
        f"This is a {task_type} session — keep steps appropriate for that specialist.\n"
        "The failed task's partial work was NOT reverted — it is still in the "
        "working tree. Inspect it (git_status/git_diff/read_file) and write the "
        "smaller steps against that half-done state: build on edits that are "
        "sound, and add an explicit fix/cleanup step for edits that are wrong. "
        "Say in each step what already exists so the worker does not redo it.\n"
        "Keep plan sections (Desired end state, Out of scope, Key discoveries, etc.) "
        "unchanged unless new evidence from read_file/grep requires updates.\n"
        "If the failure reason contradicts your assumptions, grep/read_file to verify "
        "before revising the plan.\n"
        "Keep already-done items unchanged. Do not add steps that duplicate ones "
        "already in the list, and do not introduce new design-doc/planning steps. "
        "The smaller steps must directly address what went wrong below.\n"
        "Put the FULL revised plan in a ```markdown fence in your final reply.\n"
        "Do not ask the user — note ambiguities under Open questions.\n\n"
        f"Current todo_list:\n{todo or '(empty)'}\n\n"
        f"Failed task: {failed_task}\n\n"
        f"What went wrong:\n{reason[:3000]}"
    )


class PlannerSession:
    def __init__(
        self,
        api_key,
        model,
        system_prompt,
        tool_schemas,
        tools,
        label,
        *,
        trace_sink=None,
        run_log_path=None,
        turn_id=None,
    ):
        self.api_key = api_key
        self.model = model
        self.tool_schemas = tool_schemas
        self.tools = tools
        self.label = label
        self.trace_sink = trace_sink
        self.run_log_path = run_log_path
        self.turn_id = turn_id
        self._planner_system_prompt = system_prompt
        self.messages, self.context, self.worklog_id = init_agent_context(
            system_prompt=system_prompt,
            run_log_path=run_log_path,
            label=label,
        )
        self.step = 0

    def send(self, user_prompt):
        from langbridge_code.skills import (
            PLANNER_SKILL_NAMES,
            ensure_skill_index_block,
            skill_catalog_text_for,
        )

        ensure_skill_index_block(
            self.context.stack,
            self.api_key,
            self.model,
            user_prompt,
            skill_catalog_text_for(PLANNER_SKILL_NAMES),
            label=f"{self.label} skill prefetch",
        )
        self.context.begin_turn(user_prompt)
        write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, user_prompt)
        foreground = ForegroundTracker(self.label, self.messages, self.model)
        foreground.activate()
        start_time = now()
        try:
            for _ in range(MAX_PLANNER_STEPS):
                control.checkpoint()
                if over_time_budget(start_time, MAX_PLANNER_SECONDS):
                    return self._finish("Planner stopped: out of time.")
                self.context.compact_to_budget(api_key=self.api_key, model=self.model)
                budget = prepare_agent_messages(
                    self.messages,
                    self.model,
                    base_system_prompt=self._planner_system_prompt,
                )
                foreground.publish()
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
                print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                if not tool_calls:
                    if output:
                        finish_step(self.context, list(output), self, budget)
                        foreground.publish()
                    return self._finish(extract_output_text(output))
                write_worklog_step(self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, output)
                step_items = list(output)
                for call in tool_calls:
                    tool_output = self._run_tool(call)
                    step_items.append(tool_output)
                    write_worklog_observation(
                        self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, tool_output
                    )
                finish_step(self.context, step_items, self, budget)
                foreground.publish()
                self.step += 1
            return self._finish("Planner stopped: max steps.")
        finally:
            foreground.deactivate()

    def _run_tool(self, call):
        name = call.get("name")
        call_id = call.get("call_id")
        try:
            arguments = without_purpose(json.loads(call.get("arguments") or "{}"))
            if name not in self.tools:
                raise ValueError(f"Unknown planner tool: {name}")
            output = self.tools[name](**arguments)
        except Exception as error:
            output = f"Tool error: {error}"
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    def _finish(self, report):
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        return report


def dispatch_planner(
    task,
    description="",
    *,
    api_key,
    model,
    run_log_path,
    turn_id,
    trace_sink=None,
    phase_sink=None,
    replace_existing_plan=False,
    **kwargs,
):
    if not replace_existing_plan:
        blocked = planner_replace_blocked_message(run_log_path)
        if blocked:
            return blocked
    emit_phase(phase_sink, "planning")
    report = run_planner(
        api_key,
        model,
        initial_plan_prompt(task),
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )
    plan_md = extract_plan_markdown(report)
    task_type = parse_plan_task_type(report)
    type_label = task_type or "unknown"
    review_lines = [
        f"[{description or 'planner'}] Plan DRAFT ready (not committed).",
        f"Suggested PLAN_TASK_TYPE: {type_label}.",
        "",
        "Main agent MUST:",
        "1. Review this draft as if you wrote it (scope, depends, verify, Open questions).",
        "2. ask_user if anything is ambiguous — do not guess.",
        "3. Call update_plan with the final markdown (edited as needed).",
        "   Start the content with "
        f"`<!-- task_type: {type_label if type_label in {'coding', 'slide'} else 'coding'} -->`.",
        "4. Only then read_plan / agent_worker.",
    ]
    if plan_md:
        review_lines.append(f"Extracted draft length: {len(plan_md)} chars (see fenced plan below).")
    else:
        review_lines.append(
            "No fenced plan found — recover the full markdown from the report, then update_plan."
        )
    header = "\n".join(review_lines)
    return f"{header}\n\n{report[:6000]}"


def build_agent_planner_tool(
    *,
    api_key,
    model,
    run_log_path,
    turn_id,
    trace_sink=None,
    phase_sink=None,
    question_callback=None,
    **kwargs,
):
    def agent_planner(prompt, description="", replace_existing_plan=False):
        return dispatch_planner(
            prompt,
            description=description,
            api_key=api_key,
            model=model,
            run_log_path=run_log_path,
            turn_id=turn_id,
            trace_sink=trace_sink,
            phase_sink=phase_sink,
            replace_existing_plan=replace_existing_plan,
        )

    return agent_planner
