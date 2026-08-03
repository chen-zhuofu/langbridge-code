"""Explore subagent loop (agent_explorer tool implementation)."""
import json

import subprocess
from pathlib import Path

from langbridge_code.agents.common import control
from langbridge_code.agents.common.limits import now, over_time_budget
from langbridge_code.agents.common.task_progress import TaskProgress
from langbridge_code.prompt.system import explorer_system_prompt
from langbridge_code.tools.note_progress import TASK_NOTE_PROGRESS_TOOL_SCHEMA
from langbridge_code.llm.client import create_model_response
from langbridge_code.llm.parse import extract_output_text, print_step_trace
from langbridge_code.tools.common.description import without_description
from langbridge_code.tools.common.runtime import managed_binary
from langbridge_code.util.agent_worklog import (
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
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
EXPLORE_TOOL_SCHEMAS = skills.schemas_with_role(
    [
        schema
        for schema in (
            filesystem.TOOL_SCHEMAS
            + execution.TOOL_SCHEMAS
            + web.TOOL_SCHEMAS
            + skills.TOOL_SCHEMAS
        )
        if schema["name"] in EXPLORE_TOOL_NAMES
    ],
    "explorer",
)



def read_only_bash(**kwargs):
    return execution.read_only_bash(role="Explore agent", **kwargs)


EXPLORE_TOOLS = skills.tools_with_role(
    {
        name: tool
        for name, tool in (
            filesystem.TOOLS
            | web.TOOLS
            | skills.TOOLS
        ).items()
        if name in EXPLORE_TOOL_NAMES and name != "bash"
    },
    "explorer",
)
EXPLORE_TOOLS["bash"] = read_only_bash


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


def build_explore_prompt(task: str, *, thoroughness="", cwd=None) -> str:
    """Assemble the explorer user message: git context, caller depth, task."""
    parts = []
    git_context = collect_git_context(cwd)
    if git_context:
        parts.append(git_context)
    depth = (thoroughness or "").strip()
    if depth:
        # Forward the caller's own instructions as-is (no canned enum expansion).
        if not depth.lower().startswith("thoroughness"):
            depth = f"Thoroughness: {depth}"
        parts.append(depth)
    parts.append(task.strip())
    return "\n\n".join(part for part in parts if part)


AGENT_EXPLORER_TOOL_SCHEMA = {
    "type": "function",
    "name": "agent_explorer",
    "description": (
        "Read-only codebase mapping; returns a short findings summary (paths / what\n"
        "matters) — not the explore trace or file dumps. Never parallelize with\n"
        "agent_planner.\n"
        "\n"
        "When to use:\n"
        "- Concrete map questions: where is X, which files own Y, how is Z wired.\n"
        "- You expect more than about 3 search/read hops, or several independent\n"
        "  questions.\n"
        "\n"
        "When not to use:\n"
        "- You already know the path or symbol — grep/read_file yourself.\n"
        "- One or two tool calls would answer it.\n"
        "- Any edit or implementation — those stay with you or agent_worker."
    ),
    "parameters": {
        "type": "object",
        "properties": {
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
                    "Stable id for this investigation (e.g. 'explore-auth-flow'). "
                    "Keys the progress note and traces — reuse the exact id when "
                    "continuing the same investigation; use a new id for a new one."
                ),
            },
            "thoroughness": {
                "type": "string",
                "enum": ["quick", "medium", "thorough"],
                "description": (
                    'How deep to search: "quick" for basic searches, "medium" for '
                    "moderate exploration, or \"thorough\" for comprehensive "
                    "analysis across multiple locations and naming conventions."
                ),
            },
        },
        "required": ["prompt", "description", "task_name", "thoroughness"],
        "additionalProperties": False,
    },
}


# Mirrors Claude Code's AgentTool limits: inline up to ~50K chars; larger
# reports are persisted to a file and the caller gets a preview + path.
EXPLORE_REPORT_MAX_CHARS = 50_000
EXPLORE_REPORT_PREVIEW_CHARS = 2_000


def write_report_copy(run_log_path, task_name, instance_id, report) -> Path | None:
    """Save the final report under explorer/reports/ for main to read_file.

    Explorer itself does not get this path in its readable roots — only main
    may follow the absolute path when the inline result overflows.
    """
    from langbridge_code.util.artifacts import explorer_report_path

    if not (report or "").strip() or instance_id is None:
        return None
    dest = explorer_report_path(run_log_path, task_name, instance_id)
    if dest is None:
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(report, encoding="utf-8")
    except OSError:
        return None
    return dest


def _preview(text: str, max_chars: int) -> str:
    """First max_chars of text, cut at a newline boundary when one is nearby."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_newline = truncated.rfind("\n")
    cut = last_newline if last_newline > max_chars * 0.5 else max_chars
    return text[:cut]


def format_explore_output(
    description,
    report,
    *,
    max_chars=EXPLORE_REPORT_MAX_CHARS,
    report_path=None,
):
    title = (description or "explore").strip() or "explore"
    report = report or ""
    if len(report) <= max_chars:
        return f"[{title}] Explore findings:\n\n{report}"
    preview = _preview(report, EXPLORE_REPORT_PREVIEW_CHARS)
    if report_path is None:
        return (
            f"[{title}] Explore findings (truncated to {max_chars} chars):\n\n"
            f"{report[:max_chars]}"
        )
    return (
        f"[{title}] Explore findings: the report is {len(report)} chars, too large "
        f"to inline. The full report was saved to {report_path} — read it with "
        f"read_file (use offset/limit for sections).\n\n"
        f"Preview (first ~{EXPLORE_REPORT_PREVIEW_CHARS} chars):\n\n{preview}"
    )


def run_explore(
    api_key,
    model,
    prompt: str,
    *,
    thoroughness="",
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    task_name="",
) -> tuple[str, Path | None]:
    """Run one explore dispatch; return (report, persisted report path or None)."""
    from langbridge_code.agents.common.workspace import nested_agent_artifacts

    with nested_agent_artifacts(run_log_path, label="Explore", task_name=task_name):
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
        report = session.send(build_explore_prompt(prompt, thoroughness=thoroughness))
        return report, session.report_path


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
        self.report_path: Path | None = None
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
            self.task_progress.attach(
                self.context.stack, self.messages, self.tool_schemas
            )
        self.step = 0

    def send(self, user_prompt):
        from langbridge_code.skills import (
            attach_skill_tracking,
            ensure_skill_index_block,
            explorer_skill_catalog,
        )

        ensure_skill_index_block(
            self.context.stack,
            self.api_key,
            self.model,
            user_prompt,
            explorer_skill_catalog(),
            label=f"{self.label} skill listing",
        )
        attach_skill_tracking(self.context.stack, self.tools, role="explorer")
        self.context.begin_turn(user_prompt)
        write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, user_prompt)
        foreground = ForegroundTracker(self.label, self.messages, self.model)
        foreground.activate()
        start_time = now()
        try:
            # None steps/seconds = unlimited (default). Optional caps still use
            # _budget_stop_report so a configured limit is not a wasted call.
            while True:
                if MAX_EXPLORER_STEPS is not None and self.step >= MAX_EXPLORER_STEPS:
                    return self._finish(self._budget_stop_report("max steps"))
                control.checkpoint()
                if over_time_budget(start_time, MAX_EXPLORER_SECONDS):
                    return self._finish(self._budget_stop_report("out of time"))
                self.context.compact_to_budget(model=self.model)
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
                step_items = list(output)
                from langbridge_code.agents.common.parallel_tools import run_tool_calls

                for tool_output in run_tool_calls(self._run_tool, tool_calls):
                    step_items.append(tool_output)
                    write_worklog_observation(
                        self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, tool_output
                    )
                self.step += 1
                finish_step(self.context, step_items, self, budget)
                self.task_progress.maybe_force_write(self.context)
                foreground.publish()
        finally:
            foreground.deactivate()

    def _run_tool(self, call):
        name = call.get("name")
        call_id = call.get("call_id")
        try:
            arguments = without_description(json.loads(call.get("arguments") or "{}"), name)
            if name not in self.tools:
                raise ValueError(f"Unknown Explore tool: {name}")
            from langbridge_code.tools.execution import attach_run_log_path

            attach_run_log_path(name, arguments, self.run_log_path)
            output = self.tools[name](**arguments)
        except Exception as error:
            output = f"Tool error: {error}"
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    def _budget_stop_report(self, reason: str) -> str:
        """Return partial findings when the explore budget ends — not a stub.

        One no-tool finalize call asks for the normal report shape from what is
        already in context. If that fails, fall back to task progress notes so
        the parent still gets something reusable.
        """
        header = (
            f"{self.label} stopped early ({reason}). Partial findings below — "
            "the search did not finish; verify before relying on them.\n\n"
        )
        finalized = self._finalize_partial_report(reason)
        if finalized:
            return header + finalized
        notes = self._progress_notes_fallback()
        if notes:
            return header + "## Progress notes so far\n\n" + notes
        return header + "(No findings were recorded before the budget ended.)"

    def _finalize_partial_report(self, reason: str) -> str:
        instruction = (
            f"Your search budget ended ({reason}). Using ONLY evidence already "
            "gathered in this conversation, write the final report now with "
            "## Findings, ## Answer, and ## Open questions. Do not call tools. "
            "Mark gaps under Open questions."
        )
        try:
            self.messages.append({"role": "user", "content": instruction})
            response = control.run_interruptible(
                lambda: create_model_response(
                    self.api_key,
                    self.model,
                    messages_with_budget_notice(self.messages, self.model),
                    tool_schemas=None,
                    reasoning={"summary": "auto"},
                    label=f"{self.label} finalize",
                    stream_sink=self.trace_sink,
                )
            )
            output = response.get("output", [])
            print_step_trace(output, include_message=True, label=f"{self.label} finalize", sink=self.trace_sink)
            text = (extract_output_text(output) or "").strip()
            return text
        except control.TurnAborted:
            raise
        except Exception:
            return ""

    def _progress_notes_fallback(self) -> str:
        if not self.task_progress.enabled:
            return ""
        from langbridge_code.util.progress import PROGRESS_HEADER, read_progress

        content = read_progress(
            self.run_log_path,
            self.task_progress.task_name,
            role=self.label,
        ).strip()
        if not content or content == PROGRESS_HEADER.strip():
            return ""
        return content

    def _finish(self, report):
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        self.report_path = write_report_copy(
            self.run_log_path,
            self.task_progress.task_name,
            self.context.agent_trace_instance_id,
            report,
        )
        return report


def dispatch_explore(
    api_key,
    model,
    prompt,
    *,
    description="",
    thoroughness="",
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
    report, report_path = run_explore(
        api_key,
        model,
        task,
        thoroughness=thoroughness,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        task_name=task_name,
    )
    return format_explore_output(description, report, report_path=report_path)


def build_agent_explorer_tool(
    *,
    api_key,
    model,
    run_log_path=None,
    turn_id=None,
    trace_sink=None,
    phase_sink=None,
):
    def agent_explorer(prompt, description="", thoroughness="", task_name=""):
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
