import copy
import inspect
import json
import sys

from openai import OpenAI, OpenAIError

from langbridge_cli.config import MAX_AGENT_STEPS, MAX_L4_L3_TURNS, MAX_RALPH_LOOPS, WRITE_TOOLS
from langbridge_cli.debug import print_llm_request, print_llm_response
from langbridge_cli.roles import SYSTEM_PROMPT
from langbridge_cli.tools.plan import read_todo_list
from langbridge_cli.logging import (
    write_finish_log,
    write_input_log,
    write_tool_calls_log,
    write_tool_calls_result_log,
)
from langbridge_cli.parse import extract_output_text, print_step_trace
from langbridge_cli.tool_schema import strip_tool_purpose
from langbridge_cli.tools import MAIN_TOOL_SCHEMAS, MAIN_TOOLS
from langbridge_cli.trajectory import (
    write_trajectory_finish,
    write_trajectory_observation,
    write_trajectory_step,
)
from langbridge_cli.worklog import append_worklog_entry, start_worklog


def run_ralph_loop(
    api_key,
    model,
    target,
    run_log_path,
    turn_id,
    trace_sink=None,
    print_reply=True,
    approval_callback=None,
):
    finished = ""
    for _ in range(MAX_RALPH_LOOPS):
        round_input = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ralph_round_prompt(target, read_todo_list())},
        ]
        finished = run_agent(
            api_key,
            model,
            round_input,
            run_log_path,
            turn_id,
            trace_sink=trace_sink,
            print_reply=print_reply,
            approval_callback=approval_callback,
        )
        if not ralph_should_continue(finished):
            break
    return finished


def ralph_round_prompt(target, todo_list):
    parts = [f"Task from the user:\n{target}"]
    if todo_list:
        parts.append(f"Current todo_list:\n{todo_list}")
    else:
        parts.append("There is no todo_list yet.")
    return "\n\n".join(parts)


def ralph_should_continue(finished):
    for line in reversed(finished.strip().splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped.upper() == "RALPH_STATUS: CONTINUE"
    return False


def run_agent(
    api_key,
    model,
    input,
    run_log_path,
    turn_id,
    trace_sink=None,
    print_reply=True,
    approval_callback=None,
):
    write_input_log(run_log_path, turn_id, input) # write current message into log
    for step in range(MAX_AGENT_STEPS):
        step_response = create_response(api_key, model, input).get("output", [])
        tool_calls = [item for item in step_response if item.get("type") == "function_call"]
        print_step_trace(step_response, include_message=bool(tool_calls), label="PM agent", sink=trace_sink)

        if tool_calls:
            input.extend(step_response)
            write_tool_calls_log(run_log_path, turn_id, step, step_response) # write step_response or socalled "action" into log
            write_trajectory_step(run_log_path, "PM agent", turn_id, step, step_response)
            for call in tool_calls:
                tool_output = run_tool_call(call, api_key, model, trace_sink, approval_callback, run_log_path, turn_id)
                input.append(tool_output)
                write_tool_calls_result_log(run_log_path, turn_id, step, tool_output) # write tool_output or socalled "observation" into log
                write_trajectory_observation(run_log_path, "PM agent", turn_id, step, tool_output)
        else:
            finished = extract_output_text(step_response)
            input.append({"role": "assistant", "content": finished})
            write_finish_log(run_log_path, turn_id, finished) # write finished or socalled "agent loop ouput" into log
            write_trajectory_finish(run_log_path, "PM agent", turn_id, finished)
            if print_reply:
                print(f"\n{finished}\n")
            return finished
    finished = "Agent stopped because it reached the maximum tool-call steps."
    input.append({"role": "assistant", "content": finished})
    write_finish_log(run_log_path, turn_id, finished) #write finished or socalled "agent loop ouput" into log
    write_trajectory_finish(run_log_path, "PM agent", turn_id, finished)
    if print_reply:
        print(f"\n{finished}\n")
    return finished


def create_response(api_key, model, agent_input):
    client = OpenAI(api_key=api_key)
    print_llm_request("PM agent", model, agent_input, MAIN_TOOL_SCHEMAS)
    try:
        response = client.responses.create(
            model=model,
            input=agent_input,
            tools=MAIN_TOOL_SCHEMAS,
            reasoning={"summary": "auto"},
        )
    except OpenAIError as error:
        raise RuntimeError(str(error))

    data = response.model_dump(exclude_none=True)
    print_llm_response("PM agent", data)
    return data


def run_tool_call(call, api_key=None, model=None, trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = strip_tool_purpose(json.loads(call.get("arguments") or "{}"))
        if name not in MAIN_TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        if name in WRITE_TOOLS and not approve_write_tool(name, arguments, approval_callback):
            raise PermissionError(f"{name} was not approved")
        tool_arguments = add_hidden_tool_context(MAIN_TOOLS[name], arguments, api_key, model, trace_sink, approval_callback, run_log_path, turn_id)
        output = MAIN_TOOLS[name](**tool_arguments)
        if name == "ask_l4_engineer":
            output = append_pm_l3_review(api_key, model, arguments, output, trace_sink, run_log_path, turn_id, approval_callback)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def append_pm_l3_review(api_key, model, arguments, l4_output, trace_sink=None, run_log_path=None, turn_id=None, approval_callback=None):
    if not l4_output.startswith("L4_STATUS: READY_FOR_REVIEW"):
        return l4_output

    from langbridge_cli.multi_agent import l3_review_passed, l4_ready_for_review

    task = arguments.get("task", "")
    context = arguments.get("context", "")
    start_worklog(run_log_path, task)

    l4_report = l4_output
    append_worklog_entry(run_log_path, "L4 engineer", l4_report, "ready")

    l3_report = ""
    for _ in range(MAX_L4_L3_TURNS):
        l3_report = run_l3_review(api_key, model, task, pm_l3_review_context(context, l4_report), trace_sink, run_log_path, turn_id)
        if l3_review_passed(l3_report):
            append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "pass")
            return pm_review_result(l4_report, l3_report, "OK")
        append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "concern exist")

        l4_report = run_l4_fix(api_key, model, task, context, l3_report, trace_sink, run_log_path, turn_id, approval_callback)
        if not l4_ready_for_review(l4_report):
            append_worklog_entry(run_log_path, "L4 engineer", l4_report, "needs pm")
            return pm_review_result(l4_report, l3_report, "NEEDS_WORK")
        append_worklog_entry(run_log_path, "L4 engineer", l4_report, "ready")

    return pm_review_result(l4_report, l3_report, "NEEDS_WORK")


def run_l3_review(api_key, model, task, l3_context, trace_sink, run_log_path, turn_id):
    from langbridge_cli.multi_agent import run_l3_test_engineer

    if trace_sink is None and run_log_path is None:
        return run_l3_test_engineer(api_key, model, task, l3_context)
    return run_l3_test_engineer(
        api_key,
        model,
        task,
        l3_context,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )


def run_l4_fix(api_key, model, task, context, feedback, trace_sink, run_log_path, turn_id, approval_callback):
    from langbridge_cli.multi_agent import run_l4_engineer

    if trace_sink is None and run_log_path is None and approval_callback is None:
        return run_l4_engineer(api_key, model, task, context, feedback)
    return run_l4_engineer(
        api_key,
        model,
        task,
        context,
        feedback,
        trace_sink=trace_sink,
        approval_callback=approval_callback,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )


def pm_review_result(l4_report, l3_report, pm_status):
    return f"{l4_report}\n\nPM_DETERMINISTIC_L3_REVIEW:\n{l3_report}\n\nPM_REVIEW_STATUS: {pm_status}"


def pm_l3_review_context(context, l4_output):
    parts = []
    if context:
        parts.append(context)
    parts.append("L4 completed work and is ready for PM-triggered L3 review.")
    parts.append(f"L4 report:\n{l4_output}")
    return "\n\n".join(parts)


def add_hidden_tool_context(function, arguments, api_key, model, trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None):
    parameters = inspect.signature(function).parameters
    tool_arguments = dict(arguments)
    if "api_key" in parameters:
        tool_arguments["api_key"] = api_key
    if "model" in parameters:
        tool_arguments["model"] = model
    if "trace_sink" in parameters:
        tool_arguments["trace_sink"] = trace_sink
    if "approval_callback" in parameters:
        tool_arguments["approval_callback"] = approval_callback
    if "run_log_path" in parameters:
        tool_arguments["run_log_path"] = run_log_path
    if "turn_id" in parameters:
        tool_arguments["turn_id"] = turn_id
    return tool_arguments


def approve_write_tool(name, arguments, approval_callback=None):
    if approval_callback is not None:
        return approval_callback("PM agent", name, arguments)
    if not sys.stdin.isatty():
        return False

    print(f"\nApprove write tool: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    answer = input("Run this tool? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}
