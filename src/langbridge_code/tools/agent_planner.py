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
from langbridge_code.context.common.budget import messages_with_budget_notice, prepare_agent_messages
from langbridge_code.context.agent_context import finish_step, init_agent_context
from langbridge_code.context.foreground import ForegroundTracker
from langbridge_code.settings import (
    MAX_PLANNER_SECONDS,
    MAX_PLANNER_STEPS,
)
from langbridge_code.tools import (
    FILE_READ_TOOL_NAMES,
    execution,
    filesystem,
    skills,
    web,
)
from langbridge_code.agents.common.phases import emit_phase
from langbridge_code.agents.system_prompt.planner import PLANNER_WORKFLOW_SUMMARY, planner_system_prompt
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER


PLANNER_TOOL_NAMES = (
    FILE_READ_TOOL_NAMES
    | {"bash", "read_webpage", "read_skill"}
)
PLANNER_TOOL_SCHEMAS = [
    schema
    for schema in (
        filesystem.TOOL_SCHEMAS
        + execution.TOOL_SCHEMAS
        + web.TOOL_SCHEMAS
        + skills.TOOL_SCHEMAS
    )
    if schema["name"] in PLANNER_TOOL_NAMES
]


def planner_read_only_bash(**kwargs):
    return execution.read_only_bash(role="Planner", **kwargs)


PLANNER_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS
        | web.TOOLS
        | skills.TOOLS
    ).items()
    if name in PLANNER_TOOL_NAMES and name != "bash"
}
PLANNER_TOOLS["bash"] = planner_read_only_bash


AGENT_PLANNER_TOOL_SCHEMA = {
    "type": "function",
    "name": "agent_planner",
    "description": (
        "Offload plan research/drafting so repo exploration stays OUT of your "
        "main-agent context. You get ONE draft result back — not the planner's "
        "tool trace. Review that draft as if you wrote it; ask the user if "
        "ambiguous; then write the final plan yourself to the session-artifact "
        "virtual path todo_list.md (write tool). Do not dispatch agent_worker before "
        "todo_list.md is written. If todo_list.md already holds an unfinished "
        "plan, ask the user first whether to continue it, replace it, or start "
        "fresh — do not silently overwrite."
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
            "task_name": {
                "type": "string",
                "description": (
                    "Stable name for this planning task (e.g. 'plan-wumpus-game'). "
                    "Used to label the run; reuse the exact name when re-running "
                    "the same planning task."
                ),
            },
        },
        "required": ["purpose", "prompt", "description", "task_name"],
        "additionalProperties": False,
    },
}


def run_planner(
    api_key,
    model,
    prompt: str,
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    task_name="",
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
        task_name=task_name,
    )
    return session.send(prompt)


def initial_plan_prompt(user_task: str) -> str:
    return (
        f"{PLANNER_WORKFLOW_SUMMARY}\n"
        "Create an evidence-based plan for this user task.\n\n"
        "Before your final reply:\n"
        "1. Read user-named files and primary context FULLY (no limit/offset).\n"
        "2. grep/glob the repo; use read-only bash / read_webpage when helpful; "
        "cite `path:line` for every discovery.\n"
        "3. If assumptions are unclear, put them in Open questions — do NOT ask the user.\n\n"
        "Your final reply must put the FULL plan document in a ```markdown fenced\n"
        "block with the FULL plan: Desired end state, Success criteria,\n"
        "Key discoveries, Out of scope, Current state, Design options (if non-trivial),\n"
        "Open questions, Todo list, Changes required (file:line pointers with one-line\n"
        "intents; a short illustrative snippet only when it clarifies an interface —\n"
        "do not write the implementation, the worker writes the code).\n"
        "Keep supporting prose concise. Never shorten a task by dropping its\n"
        "requirements, acceptance criteria, deliverables, verification, or boundaries.\n"
        "You have no write access and no ask_user — the main agent writes the plan file.\n\n"
        "Every todo is a complete task contract. Its checkbox and deps note are MANDATORY:\n"
        "  - [ ] Task N: <reviewable deliverable> (deps: none | tasks N, M)\n"
        "    - Objective: <specific outcome>\n"
        "    - Detailed requirements: <all behavior and constraints>\n"
        "    - Acceptance spec: <observable binary pass/fail criteria>\n"
        "    - Deliverables: <files or artifacts>\n"
        "    - Verify: <exact commands and manual checks>\n"
        "    - Out of scope: <task-local exclusions>\n"
        "Acceptance spec defines correct behavior; Verify explains how to prove it.\n"
        "Reject vague or contradictory criteria instead of asking a worker to guess.\n"
        "Write `deps: none` only when the todo can start immediately with no other\n"
        "todo's output — e.g. a scaffold/setup todo blocks everything that edits\n"
        "the files it creates. Todos sharing one file are almost never independent.\n"
        "For coding: build and verify working software; no design docs unless asked.\n"
        "Split independent edits into separate todos; file/function-level steps are fine.\n"
        "No padding or duplicates.\n"
        "For 3+ coding implementation steps, end with a final integration verification todo.\n\n"
        "After the fenced plan, add a brief ## Summary.\n\n"
        f"User task:\n{user_task}"
    )


def parse_plan_task_type(report: str) -> str | None:
    """Legacy helper: plans are coding-only. Returns 'coding' if a type line is present."""
    for line in (report or "").strip().splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("plan_task_type:"):
            return "coding"
    return None


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
        task_name="",
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
            task_name=task_name,
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
                        messages_with_budget_notice(self.messages, self.model),
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
    task_name="",
    **kwargs,
):
    emit_phase(phase_sink, "planning")
    report = run_planner(
        api_key,
        model,
        initial_plan_prompt(task),
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        task_name=task_name,
    )
    review_lines = [
        f"[{description or 'planner'}] Plan DRAFT ready (not written to disk).",
        "",
        "Main agent MUST:",
        "1. Review this draft as if you wrote it. Every task needs Objective, Detailed "
        "requirements, Acceptance spec, Deliverables, Verify, Out of scope, and deps.",
        "2. Reject vague or contradictory acceptance criteria; ask_user when code and "
        "the request cannot resolve them — do not make a worker guess.",
        "3. Write the final markdown (edited as needed) to the session-artifact "
        "virtual path todo_list.md with the write tool.",
        "4. Only then dispatch agent_worker. Copy one complete task contract "
        "word-for-word into task_contract and put only new repository facts in "
        "supplemental_context.",
    ]
    header = "\n".join(review_lines)
    # Pass the planner's report through untruncated: cutting the draft mid-plan
    # forces the main agent to reconstruct lost sections from memory.
    return f"{header}\n\n{report}"


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
    def agent_planner(prompt, description="", task_name=""):
        return dispatch_planner(
            prompt,
            description=description,
            api_key=api_key,
            model=model,
            run_log_path=run_log_path,
            turn_id=turn_id,
            trace_sink=trace_sink,
            phase_sink=phase_sink,
            task_name=(task_name or "").strip(),
        )

    return agent_planner
