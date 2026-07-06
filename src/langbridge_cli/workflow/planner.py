"""Agentic planner: builds or refines the session todo_list."""
from langbridge_cli.agents.multi_agent import SpecialistSession, create_specialist_response
from langbridge_cli.agents.roles import planner_system_prompt
from langbridge_cli.agents.limits import now, over_context_budget, over_time_budget
from langbridge_cli.agents import control
from langbridge_cli.llm.parse import extract_output_text, print_step_trace
from langbridge_cli.llm.tool_schema import strip_tool_purpose, with_tool_purpose
from langbridge_cli.persistence.agent_worklog import (
    new_worklog_id,
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
    write_worklog_step,
)
from langbridge_cli.persistence.context import compact_messages_if_needed
from langbridge_cli.settings import (
    MAX_PLANNER_SECONDS,
    MAX_PLANNER_STEPS,
    MAX_SPECIALIST_CONTEXT_TOKENS,
)
from langbridge_cli.tools import filesystem, plan
from langbridge_cli.tools.plan import read_todo_list

PLANNER_TOOL_NAMES = {"list_dir", "glob", "read_file", "grep", "update_plan"}
PLANNER_TOOL_SCHEMAS = with_tool_purpose(
    [
        schema
        for schema in filesystem.TOOL_SCHEMAS + plan.TOOL_SCHEMAS
        if schema["name"] in PLANNER_TOOL_NAMES
    ]
)
PLANNER_TOOLS = {
    name: tool
    for name, tool in (filesystem.TOOLS | plan.TOOLS).items()
    if name in PLANNER_TOOL_NAMES
}


def run_planner(
    api_key,
    model,
    prompt: str,
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
) -> str:
    session = _new_planner_session(api_key, model, trace_sink, run_log_path, turn_id)
    return session.send(prompt)


def initial_plan_prompt(user_task: str) -> str:
    return (
        "Create a todo_list for this user task. Break hard work into several "
        "component-level tasks. Each line must be:\n"
        "  - [ ] [coding] <description>\n"
        "  - [ ] [presentation] <description>\n"
        "The last coding task should be an end-to-end test when appropriate. "
        "Call update_plan with the full markdown when ready.\n\n"
        f"User task:\n{user_task}"
    )


def refine_plan_prompt(failed_task: str, task_type: str, reason: str, todo: str) -> str:
    return (
        "The task below did not complete in the workflow outer loop. Replace ONLY "
        "that task in the todo_list with 2-4 smaller tasks of the same type. "
        "Use update_plan to write the full revised todo_list.\n\n"
        f"Current todo_list:\n{todo or '(empty)'}\n\n"
        f"Failed task [{task_type}]: {failed_task}\n\n"
        f"What went wrong:\n{reason[:3000]}"
    )


def _new_planner_session(api_key, model, trace_sink, run_log_path, turn_id):
    return PlannerSession(
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


class PlannerSession(SpecialistSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def send(self, user_prompt):
        self.messages.append({"role": "user", "content": user_prompt})
        write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, user_prompt)
        start_time = now()
        for _ in range(MAX_PLANNER_STEPS):
            control.checkpoint()
            if over_time_budget(start_time, MAX_PLANNER_SECONDS):
                return self._finish("Planner stopped: out of time.")
            if over_context_budget(self.messages, MAX_SPECIALIST_CONTEXT_TOKENS):
                return self._finish("Planner stopped: context budget exceeded.")
            response = control.run_interruptible(
                lambda: create_specialist_response(
                    self.api_key, self.model, self.messages, self.tool_schemas, self.label
                )
            )
            output = response.get("output", [])
            tool_calls = [item for item in output if item.get("type") == "function_call"]
            if not tool_calls:
                return self._finish(extract_output_text(output))
            print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
            write_worklog_step(self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, output)
            self.messages.extend(output)
            for call in tool_calls:
                tool_output = self._run_tool(call)
                self.messages.append(tool_output)
                write_worklog_observation(
                    self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, tool_output
                )
            compact_messages_if_needed(
                self.messages,
                max_context_tokens=MAX_SPECIALIST_CONTEXT_TOKENS,
                api_key=self.api_key,
                model=self.model,
                label="Planner compaction",
            )
            self.step += 1
        return self._finish("Planner stopped: max steps.")

    def _run_tool(self, call):
        import json

        name = call.get("name")
        call_id = call.get("call_id")
        try:
            arguments = strip_tool_purpose(json.loads(call.get("arguments") or "{}"))
            if name not in self.tools:
                raise ValueError(f"Unknown planner tool: {name}")
            if name == "update_plan":
                arguments["run_log_path"] = self.run_log_path
            output = self.tools[name](**arguments)
        except Exception as error:
            output = f"Tool error: {error}"
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    def _finish(self, report):
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        return report
