import json
import sys

from langbridge_code.llm.client import create_model_response
from langbridge_code.settings import (
    MAX_SPECIALIST_AGENT_STEPS,
    MAX_SPECIALIST_CONTEXT_TOKENS,
    MAX_SPECIALIST_SECONDS,
)
from langbridge_code.llm.parse import extract_output_text, print_step_trace
from langbridge_code.agents.roles import (
    CODER_ENGINEER_PROMPT,
    REVIEWER_ENGINEER_PROMPT,
    coder_system_prompt,
    reviewer_system_prompt,
    l3_system_prompt,
    l4_system_prompt,
    l5_system_prompt,
)
from langbridge_code.skills import skill_catalog_text
from langbridge_code import policy
from langbridge_code.llm.tool_schema import strip_tool_purpose, with_tool_purpose
from langbridge_code.tools import execution, filesystem, skills, testing
from langbridge_code.persistence.agent_worklog import (
    new_worklog_id,
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
    write_worklog_step,
)
from langbridge_code.agents.limits import now, over_context_budget, over_time_budget
from langbridge_code.agents import control
from langbridge_code.persistence.context import compact_messages_if_needed


L3_TOOL_NAMES = {"list_dir", "glob", "read_file", "grep", "run_tests"}
L3_TOOL_SCHEMAS = with_tool_purpose(
    [
        schema
        for schema in filesystem.TOOL_SCHEMAS + testing.TOOL_SCHEMAS
        if schema["name"] in L3_TOOL_NAMES
    ]
)
L3_TOOLS = {name: tool for name, tool in (filesystem.TOOLS | testing.TOOLS).items() if name in L3_TOOL_NAMES}

L4_TOOL_NAMES = {
    "list_dir",
    "glob",
    "read_file",
    "grep",
    "edit_file",
    "create_file",
    "delete_file",
    "run_tests",
    "bash",
    "read_skill",
}
L4_TOOL_SCHEMAS = with_tool_purpose(
    [
        schema
        for schema in filesystem.TOOL_SCHEMAS + testing.TOOL_SCHEMAS + execution.TOOL_SCHEMAS + skills.TOOL_SCHEMAS
        if schema["name"] in L4_TOOL_NAMES
    ]
)
L4_TOOLS = {
    name: tool
    for name, tool in (filesystem.TOOLS | testing.TOOLS | execution.TOOLS | skills.TOOLS).items()
    if name in L4_TOOL_NAMES
}
L4_WRITE_TOOLS = {"create_file", "delete_file", "edit_file"}

# L5 codes and tests just like L4, so it shares L4's tool set and write tools.
L5_TOOL_SCHEMAS = L4_TOOL_SCHEMAS
L5_TOOLS = L4_TOOLS
L5_WRITE_TOOLS = L4_WRITE_TOOLS

# The implementers (L4/L5) can pull skills on demand. We list the catalog in their
# system prompt and give them the read_skill tool to load one when it fits. The
# catalog and the evolver-learned guidance are read fresh per session (not frozen
# at import) so a new policy/skill checkpoint takes effect on the next run.
def _skills_note():
    catalog = skill_catalog_text()
    if not catalog:
        return ""
    return (
        "You have skills available: short playbooks of guidelines for common kinds "
        "of work. When one fits the current task, call read_skill(name) to load it "
        "and follow it before you start. Available skills:\n" + catalog
    )


def l4_system_prompt():
    return coder_system_prompt()


def l3_system_prompt():
    return reviewer_system_prompt()


def l5_system_prompt():
    return coder_system_prompt()


# The run_* helpers send ONE turn to a specialist. Pass a live `session` to keep
# the same agent alive across a loop; omit it for a one-shot agent (e.g. jury).
def run_reviewer(api_key, model, task, context="", trace_sink=None, run_log_path=None, turn_id=None, session=None):
    if session is None:
        session = new_reviewer_session(api_key, model, trace_sink=trace_sink, run_log_path=run_log_path, turn_id=turn_id)
    return session.send(reviewer_user_prompt(task, context))


def run_coder(api_key, model, task, context="", feedback="", trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None, session=None, user_prompt=None):
    if session is None:
        session = new_coder_session(api_key, model, trace_sink=trace_sink, approval_callback=approval_callback, run_log_path=run_log_path, turn_id=turn_id)
    prompt = user_prompt if user_prompt is not None else coder_user_prompt(task, context, feedback)
    return session.send(prompt)


# Legacy names
run_l3_test_engineer = run_reviewer
run_l4_engineer = run_coder
run_l5_engineer = run_coder


def reviewer_user_prompt(task, context):
    prompt = f"Task to verify:\n{task}"
    if context:
        prompt += f"\n\nReview context:\n{context}"
    return prompt


def coder_user_prompt(task, context, feedback):
    prompt = f"Task to implement:\n{task}"
    if context:
        prompt += f"\n\nAdditional context:\n{context}"
    if feedback:
        prompt += f"\n\nReviewer feedback to address:\n{feedback}"
    return prompt


# Back-compat alias
l3_user_prompt = reviewer_user_prompt
l4_l5_user_prompt = coder_user_prompt


def reviewer_review_passed(report):
    first_line = report.strip().splitlines()[0].strip().lower() if report.strip() else ""
    return first_line == "review_verdict: pass"


def coder_ready_for_review(report):
    first_line = report.strip().splitlines()[0].strip().lower() if report.strip() else ""
    return first_line in {"coder_status: ready_for_review", "l4_status: ready_for_review", "l5_status: ready_for_review"}


l3_review_passed = reviewer_review_passed
l4_ready_for_review = coder_ready_for_review
l5_ready_for_review = coder_ready_for_review


class SpecialistSession:
    """A specialist agent that stays alive across a whole agentic loop.

    Each .send() appends one user turn and runs the agent until it replies with a
    final message. The message history persists between sends, so the agent
    remembers its own tool calls/results and the prior exchange. A fresh
    SpecialistSession is a brand-new agent with no memory of any earlier one.
    """

    def __init__(self, api_key, model, system_prompt, tool_schemas, tools, label,
                 trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None,
                 write_guard=None):
        self.api_key = api_key
        self.model = model
        self.tool_schemas = tool_schemas
        self.tools = tools
        self.label = label
        self.trace_sink = trace_sink
        self.approval_callback = approval_callback
        self.run_log_path = run_log_path
        self.turn_id = turn_id
        self.write_guard = write_guard
        self.messages = [{"role": "system", "content": system_prompt}]
        self.tool_history = []
        self.step = 0
        # This instance's own worklog file, so two L3s (or L4s) in the same review
        # never write into one another's trace.
        self.worklog_id = new_worklog_id(run_log_path, label)

    def send(self, user_prompt):
        self.messages.append({"role": "user", "content": user_prompt})
        write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, user_prompt)
        start_time = now()
        for _ in range(MAX_SPECIALIST_AGENT_STEPS):
            control.checkpoint()
            if over_time_budget(start_time, MAX_SPECIALIST_SECONDS):
                return self._finish(stopped_report(self.label, "ran out of time", self.tool_history))
            if over_context_budget(self.messages, MAX_SPECIALIST_CONTEXT_TOKENS):
                return self._finish(stopped_report(self.label, "exceeded the context budget", self.tool_history))
            response = control.run_interruptible(
                lambda: create_specialist_response(self.api_key, self.model, self.messages, self.tool_schemas, self.label)
            )
            output = response.get("output", [])
            tool_calls = [item for item in output if item.get("type") == "function_call"]
            if not tool_calls:
                print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                return self._finish(extract_output_text(output))
            print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
            write_worklog_step(self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, output)
            self.messages.extend(output)
            for call in tool_calls:
                tool_output = run_specialist_tool_call(
                    call, self.tools, self.label, approval_callback=self.approval_callback, write_guard=self.write_guard
                )
                self.tool_history.append({"call": call, "output": tool_output})
                self.messages.append(tool_output)
                write_worklog_observation(self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, tool_output)
            self.step += 1
            compact_messages_if_needed(
                self.messages,
                max_context_tokens=MAX_SPECIALIST_CONTEXT_TOKENS,
                api_key=self.api_key,
                model=self.model,
                label=f"{self.label} compaction",
            )
        return self._finish(max_steps_report(self.label, self.tool_history))

    def _finish(self, report):
        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        return report


def run_specialist_agent(api_key, model, system_prompt, user_prompt, tool_schemas, tools, label,
                         trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None):
    session = SpecialistSession(
        api_key, model, system_prompt, tool_schemas, tools, label,
        trace_sink=trace_sink, approval_callback=approval_callback,
        run_log_path=run_log_path, turn_id=turn_id,
    )
    return session.send(user_prompt)


def new_reviewer_session(api_key, model, trace_sink=None, run_log_path=None, turn_id=None):
    return SpecialistSession(
        api_key, model, reviewer_system_prompt(), L3_TOOL_SCHEMAS, L3_TOOLS, "Reviewer",
        trace_sink=trace_sink, run_log_path=run_log_path, turn_id=turn_id,
    )


def new_coder_session(api_key, model, trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None, write_guard=None):
    return SpecialistSession(
        api_key, model, coder_system_prompt(), L4_TOOL_SCHEMAS, L4_TOOLS, "Coder",
        trace_sink=trace_sink, approval_callback=approval_callback, run_log_path=run_log_path, turn_id=turn_id,
        write_guard=write_guard,
    )


new_l3_session = new_reviewer_session
new_l4_session = new_coder_session
new_l5_session = new_coder_session


def create_specialist_response(api_key, model, messages, tool_schemas, label):
    return create_model_response(
        api_key,
        model,
        messages,
        tool_schemas=tool_schemas,
        reasoning={"summary": "auto"},
        label=label,
    )


def run_specialist_tool_call(call, tools, label, approval_callback=None, write_guard=None):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = strip_tool_purpose(json.loads(call.get("arguments") or "{}"))
        if name not in tools:
            raise ValueError(f"Unknown {label} tool: {name}")
        if write_guard is not None and name in L4_WRITE_TOOLS:
            guard_error = write_guard(name, arguments)
            if guard_error:
                raise PermissionError(guard_error)
        if label in ("Coder", "L4 engineer", "L5 engineer") and name in L4_WRITE_TOOLS and not approve_coder_tool_write(
            label,
            name,
            arguments,
            approval_callback,
        ):
            raise PermissionError(f"{name} was not approved")
        output = tools[name](**arguments)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def approve_coder_tool_write(label, name, arguments, approval_callback=None):
    if approval_callback is not None:
        return approval_callback(label, name, arguments)
    return approve_l4_write_tool(name, arguments)


approve_l4_tool_write = approve_coder_tool_write


def max_steps_report(label, tool_history):
    return stopped_report(label, "reached the maximum specialist tool-call steps", tool_history)


def stopped_report(label, reason, tool_history):
    header = f"{label} stopped because it {reason}."
    if label in ("Coder", "L4 engineer"):
        header = "CODER_STATUS: IN_PROGRESS\nSummary: " + header
    elif label in ("L5 engineer",):
        header = "L5_STATUS: IN_PROGRESS\nSummary: " + header
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


def approve_l4_write_tool(name, arguments):
    if not sys.stdin.isatty():
        return False

    print(f"\nApprove L4 write tool: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    answer = input("Allow L4 to run this write tool? [y/N] ")
    if answer.strip().lower() in {"y", "yes"}:
        return True
    # Denying at the prompt aborts the whole turn and returns to the REPL.
    raise control.TurnAborted(f"{name} was denied.")
