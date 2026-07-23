"""Explore subagent loop (agent_explorer tool implementation)."""
import json
import subprocess
from pathlib import Path

from langbridge_code.agents.common import control
from langbridge_code.agents.common.limits import now, over_time_budget
from langbridge_code.agents.common.task_progress import TaskProgress
from langbridge_code.agents.system_prompt.explorer import explorer_system_prompt
from langbridge_code.tools.note_progress import TASK_NOTE_PROGRESS_TOOL_SCHEMA
from langbridge_code.llm.client import create_model_response
from langbridge_code.llm.parse import extract_output_text, print_step_trace
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER, without_purpose
from langbridge_code.tools.common.runtime import managed_binary
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
    MAX_EXPLORER_SECONDS,
    MAX_EXPLORER_STEPS,
    WORKSPACE_ROOT,
)
from langbridge_code.tools import FILE_READ_TOOL_NAMES
from langbridge_code.tools import execution, filesystem, skills, web
from langbridge_code.agents.common.phases import emit_phase

EXPLORE_TOOL_NAMES = (
    FILE_READ_TOOL_NAMES
    | {"bash", "read_webpage", "read_skill"}
)
EXPLORE_TOOL_SCHEMAS = [
    schema
    for schema in (
        filesystem.TOOL_SCHEMAS
        + execution.TOOL_SCHEMAS
        + web.TOOL_SCHEMAS
        + skills.TOOL_SCHEMAS
    )
    if schema["name"] in EXPLORE_TOOL_NAMES
]


def explore_bash_guard(arguments):
    return execution.bash_write_guard(
        arguments.get("command") or "",
        role="Explore agent",
    )


def read_only_bash(**kwargs):
    return execution.read_only_bash(role="Explore agent", **kwargs)


EXPLORE_TOOLS = {
    name: tool
    for name, tool in (
        filesystem.TOOLS
        | web.TOOLS
        | skills.TOOLS
    ).items()
    if name in EXPLORE_TOOL_NAMES and name != "bash"
}
EXPLORE_TOOLS["bash"] = read_only_bash

THOROUGHNESS_HINTS = {
    "quick": "Thoroughness: quick — minimal searches, answer if obvious.",
    "medium": "Thoroughness: medium — check likely locations and key files.",
    "thorough": "Thoroughness: thorough — broad search across naming variants and related modules.",
}


def _run_git(args, *, cwd):
    try:
        result = subprocess.run(
            [managed_binary("git"), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def collect_git_context(cwd=None) -> str:
    """Read-only git snapshot for explore orientation (Kimi-style git-context block)."""
    root = Path(cwd or WORKSPACE_ROOT)
    if not (root / ".git").exists():
        return ""

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    commit = _run_git(["rev-parse", "--short", "HEAD"], cwd=root)
    status = _run_git(["status", "--short"], cwd=root)
    recent = _run_git(["log", "--oneline", "-5"], cwd=root)

    lines = ["<git-context>"]
    if branch:
        lines.append(f"branch: {branch}")
    if commit:
        lines.append(f"commit: {commit}")
    if status is not None:
        if status:
            lines.append("status:")
            lines.extend(f"  {line}" for line in status.splitlines()[:20])
        else:
            lines.append("status: clean")
    if recent:
        lines.append("recent commits:")
        lines.extend(f"  {line}" for line in recent.splitlines())
    lines.append("</git-context>")
    return "\n".join(lines)


def build_explore_prompt(task: str, *, thoroughness="medium", cwd=None) -> str:
    parts = []
    git_context = collect_git_context(cwd)
    if git_context:
        parts.append(git_context)
    hint = THOROUGHNESS_HINTS.get((thoroughness or "medium").strip().lower(), THOROUGHNESS_HINTS["medium"])
    parts.append(hint)
    parts.append(task.strip())
    return "\n\n".join(part for part in parts if part)


AGENT_EXPLORER_TOOL_SCHEMA = {
    "type": "function",
    "name": "agent_explorer",
    "description": (
        "Offload read-only codebase investigation so greps/reads stay OUT of your "
        "main-agent context. You get ONE findings summary back — not the explore "
        "trace. Ask for concrete, reusable findings (file paths, key "
        "functions/classes, line ranges) and forward the relevant parts verbatim "
        "when you later dispatch agent_worker, so workers do not repeat the "
        "exploration. Use for broad search across files or naming patterns. "
        "Multiple explorer calls in one turn may run in parallel (read-only). Do "
        "not parallelize planner or worker. Prefer this over doing large "
        "explorations yourself."
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
            "task_name": {
                "type": "string",
                "description": (
                    "Stable name for this investigation (e.g. 'explore-auth-flow'). "
                    "Names the task's progress note file: findings noted there are "
                    "shown to the next explorer dispatched with the SAME task_name — "
                    "reuse the exact name when continuing an investigation."
                ),
            },
            "thoroughness": {
                "type": "string",
                "enum": ["quick", "medium", "thorough"],
                "description": "Search depth (default medium).",
            },
        },
        "required": ["purpose", "prompt", "description", "task_name"],
        "additionalProperties": False,
    },
}


def format_explore_output(description, report, *, max_chars=6000):
    title = (description or "explore").strip() or "explore"
    return f"[{title}] Explore findings:\n\n{(report or '')[:max_chars]}"


def run_explore(
    api_key,
    model,
    prompt: str,
    *,
    thoroughness="medium",
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    task_name="",
) -> str:
    session = ExploreSession(
        api_key,
        model,
        EXPLORE_TOOL_SCHEMAS,
        EXPLORE_TOOLS,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        task_name=task_name,
    )
    return session.send(build_explore_prompt(prompt, thoroughness=thoroughness))


class ExploreSession:
    def __init__(
        self,
        api_key,
        model,
        tool_schemas,
        tools,
        *,
        trace_sink=None,
        run_log_path=None,
        turn_id=None,
        task_name="",
    ):
        self.api_key = api_key
        self.model = model
        self.label = "Explore"
        self.trace_sink = trace_sink
        self.run_log_path = run_log_path
        self.turn_id = turn_id
        self._explorer_system_prompt = explorer_system_prompt()
        self.messages, self.context, self.worklog_id = init_agent_context(
            system_prompt=self._explorer_system_prompt,
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
        if self.task_progress.enabled:
            self.tools["note_progress"] = self.task_progress.write_note
            self.tool_schemas.append(TASK_NOTE_PROGRESS_TOOL_SCHEMA)
            self.task_progress.attach(self.context.stack, self.messages)
        self.step = 0

    def send(self, user_prompt):
        from langbridge_code.skills import (
            EXPLORER_SKILL_NAMES,
            ensure_skill_index_block,
            skill_catalog_text_for,
        )

        ensure_skill_index_block(
            self.context.stack,
            self.api_key,
            self.model,
            user_prompt,
            skill_catalog_text_for(EXPLORER_SKILL_NAMES),
            label=f"{self.label} skill prefetch",
        )
        self.context.begin_turn(user_prompt)
        write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, user_prompt)
        foreground = ForegroundTracker(self.label, self.messages, self.model)
        foreground.activate()
        start_time = now()
        try:
            for _ in range(MAX_EXPLORER_STEPS):
                control.checkpoint()
                if over_time_budget(start_time, MAX_EXPLORER_SECONDS):
                    return self._finish(f"{self.label} stopped: out of time.")
                self.context.compact_to_budget(api_key=self.api_key, model=self.model)
                budget = prepare_agent_messages(
                    self.messages,
                    self.model,
                    base_system_prompt=self._explorer_system_prompt,
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
                if not tool_calls:
                    print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                    if output:
                        finish_step(self.context, list(output), self, budget)
                        foreground.publish()
                    return self._finish(extract_output_text(output))
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
                self.task_progress.maybe_remind(self.context)
                foreground.publish()
            return self._finish(f"{self.label} stopped: max steps.")
        finally:
            foreground.deactivate()

    def _run_tool(self, call):
        name = call.get("name")
        call_id = call.get("call_id")
        try:
            arguments = without_purpose(json.loads(call.get("arguments") or "{}"))
            if name not in self.tools:
                raise ValueError(f"Unknown Explore tool: {name}")
            output = self.tools[name](**arguments)
        except Exception as error:
            output = f"Tool error: {error}"
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    def _finish(self, report):
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        return report


def dispatch_explore(
    api_key,
    model,
    prompt,
    *,
    description="",
    thoroughness="medium",
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    phase_sink=None,
    task_name="",
):
    task = (prompt or "").strip()
    if not task:
        return "Tool error: prompt must be a non-empty string."
    emit_phase(phase_sink, "exploring")
    report = run_explore(
        api_key,
        model,
        task,
        thoroughness=thoroughness,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        task_name=task_name,
    )
    return format_explore_output(description, report)


def build_agent_explorer_tool(
    *,
    api_key,
    model,
    run_log_path=None,
    turn_id=None,
    trace_sink=None,
    phase_sink=None,
):
    def agent_explorer(prompt, description="", thoroughness="medium", task_name=""):
        return dispatch_explore(
            api_key,
            model,
            prompt,
            description=description,
            thoroughness=thoroughness,
            trace_sink=trace_sink,
            run_log_path=run_log_path,
            turn_id=turn_id,
            phase_sink=phase_sink,
            task_name=(task_name or "").strip(),
        )

    return agent_explorer
