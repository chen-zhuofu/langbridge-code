"""Presenter agentic loop for presentation (pptx) tasks."""
from langbridge_code.agents.multi_agent import SpecialistSession, create_specialist_response
from langbridge_code.agents.roles import presenter_system_prompt
from langbridge_code.agents.limits import now, over_context_budget, over_time_budget
from langbridge_code.agents import control
from langbridge_code.llm.parse import extract_output_text, print_step_trace
from langbridge_code.llm.tool_schema import strip_tool_purpose, with_tool_purpose
from langbridge_code.persistence.agent_worklog import (
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
    write_worklog_step,
)
from langbridge_code.persistence.context import compact_messages_if_needed
from langbridge_code.settings import (
    MAX_PRESENTER_SECONDS,
    MAX_PRESENTER_STEPS,
    MAX_SPECIALIST_CONTEXT_TOKENS,
)
from langbridge_code.tools import execution, filesystem, skills

PRESENTER_TOOL_NAMES = {
    "list_dir",
    "glob",
    "read_file",
    "grep",
    "create_file",
    "edit_file",
    "bash",
    "read_skill",
}
PRESENTER_TOOL_SCHEMAS = with_tool_purpose(
    [
        schema
        for schema in filesystem.TOOL_SCHEMAS + execution.TOOL_SCHEMAS + skills.TOOL_SCHEMAS
        if schema["name"] in PRESENTER_TOOL_NAMES
    ]
)
PRESENTER_TOOLS = {
    name: tool
    for name, tool in (filesystem.TOOLS | execution.TOOLS | skills.TOOLS).items()
    if name in PRESENTER_TOOL_NAMES
}
PRESENTER_WRITE_TOOLS = {"create_file", "edit_file"}


def run_presenter_task(
    api_key,
    model,
    task: str,
    context: str = "",
    trace_sink=None,
    approval_callback=None,
    run_log_path=None,
    turn_id=None,
) -> tuple[bool, str]:
    session = PresenterSession(
        api_key,
        model,
        presenter_system_prompt(),
        PRESENTER_TOOL_SCHEMAS,
        PRESENTER_TOOLS,
        "Presenter",
        trace_sink=trace_sink,
        approval_callback=approval_callback,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )
    prompt = f"Presentation task:\n{task}"
    if context:
        prompt += f"\n\nContext:\n{context}"
    prompt += (
        "\n\nDeliver a .pptx file in the workspace. Use bash to run python-pptx "
        "or another tool if needed. End with PRESENTER_STATUS: COMPLETE when done, "
        "or PRESENTER_STATUS: IN_PROGRESS if blocked."
    )
    report = session.send(prompt)
    passed = report.strip().lower().startswith("presenter_status: complete")
    return passed, report


class PresenterSession(SpecialistSession):
    def __init__(self, *args, approval_callback=None, **kwargs):
        super().__init__(*args, approval_callback=approval_callback, **kwargs)
        self.approval_callback = approval_callback

    def send(self, user_prompt):
        self.messages.append({"role": "user", "content": user_prompt})
        write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, user_prompt)
        start_time = now()
        for _ in range(MAX_PRESENTER_STEPS):
            control.checkpoint()
            if over_time_budget(start_time, MAX_PRESENTER_SECONDS):
                return self._finish("Presenter stopped: out of time.")
            if over_context_budget(self.messages, MAX_SPECIALIST_CONTEXT_TOKENS):
                return self._finish("Presenter stopped: context budget exceeded.")
            response = control.run_interruptible(
                lambda: create_specialist_response(
                    self.api_key, self.model, self.messages, self.tool_schemas, self.label
                )
            )
            output = response.get("output", [])
            tool_calls = [item for item in output if item.get("type") == "function_call"]
            print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
            if not tool_calls:
                return self._finish(extract_output_text(output))
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
                label="Presenter compaction",
            )
            self.step += 1
        return self._finish("Presenter stopped: max steps.")

    def _run_tool(self, call):
        import json
        import sys

        from langbridge_code.agents import control

        name = call.get("name")
        call_id = call.get("call_id")
        try:
            arguments = strip_tool_purpose(json.loads(call.get("arguments") or "{}"))
            if name not in self.tools:
                raise ValueError(f"Unknown presenter tool: {name}")
            if name in PRESENTER_WRITE_TOOLS:
                if self.approval_callback is not None:
                    if not self.approval_callback(self.label, name, arguments):
                        raise PermissionError(f"{name} was not approved")
                elif sys.stdin.isatty():
                    print(f"\nApprove presenter write: {name}")
                    answer = input("Allow? [y/N] ")
                    if answer.strip().lower() not in {"y", "yes"}:
                        raise control.TurnAborted(f"{name} was denied.")
            output = self.tools[name](**arguments)
        except Exception as error:
            output = f"Tool error: {error}"
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    def _finish(self, report):
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        return report
