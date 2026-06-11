import copy
import inspect
import json
import sys

from openai import OpenAI, OpenAIError

from langbridge_cli.config import MAX_AGENT_STEPS, WRITE_TOOLS
from langbridge_cli.debug import print_llm_request, print_llm_response
from langbridge_cli.logging import (
    write_finish_log,
    write_input_log,
    write_tool_calls_log,
    write_tool_calls_result_log,
)
from langbridge_cli.parse import extract_output_text, print_step_trace
from langbridge_cli.tool_schema import strip_tool_purpose
from langbridge_cli.tools import MAIN_TOOL_SCHEMAS, MAIN_TOOLS


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
            for call in tool_calls:
                tool_output = run_tool_call(call, api_key, model, trace_sink, approval_callback)
                input.append(tool_output)
                write_tool_calls_result_log(run_log_path, turn_id, step, tool_output) # write tool_output or socalled "observation" into log
        else:
            finished = extract_output_text(step_response)
            input.append({"role": "assistant", "content": finished})
            write_finish_log(run_log_path, turn_id, finished) # write finished or socalled "agent loop ouput" into log
            if print_reply:
                print(f"\n{finished}\n")
            return finished
    finished = "Agent stopped because it reached the maximum tool-call steps."
    input.append({"role": "assistant", "content": finished})
    write_finish_log(run_log_path, turn_id, finished) #write finished or socalled "agent loop ouput" into log
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


def run_tool_call(call, api_key=None, model=None, trace_sink=None, approval_callback=None):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = strip_tool_purpose(json.loads(call.get("arguments") or "{}"))
        if name not in MAIN_TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        if name in WRITE_TOOLS and not approve_write_tool(name, arguments, approval_callback):
            raise PermissionError(f"{name} was not approved")
        tool_arguments = add_hidden_tool_context(MAIN_TOOLS[name], arguments, api_key, model, trace_sink, approval_callback)
        output = MAIN_TOOLS[name](**tool_arguments)
        if name == "ask_l4_engineer":
            output = append_pm_l3_review(api_key, model, arguments, output, trace_sink)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def append_pm_l3_review(api_key, model, arguments, l4_output, trace_sink=None):
    if not l4_output.startswith("L4_STATUS: READY_FOR_REVIEW"):
        return l4_output

    from langbridge_cli.multi_agent import l3_review_passed, run_l3_test_engineer

    l3_context = pm_l3_review_context(arguments.get("context", ""), l4_output)
    if trace_sink is None:
        l3_report = run_l3_test_engineer(api_key, model, arguments.get("task", ""), l3_context)
    else:
        l3_report = run_l3_test_engineer(
            api_key,
            model,
            arguments.get("task", ""),
            l3_context,
            trace_sink=trace_sink,
        )
    pm_status = "OK" if l3_review_passed(l3_report) else "NEEDS_WORK"
    return f"{l4_output}\n\nPM_DETERMINISTIC_L3_REVIEW:\n{l3_report}\n\nPM_REVIEW_STATUS: {pm_status}"


def pm_l3_review_context(context, l4_output):
    parts = []
    if context:
        parts.append(context)
    parts.append("L4 completed work and is ready for PM-triggered L3 review.")
    parts.append(f"L4 report:\n{l4_output}")
    return "\n\n".join(parts)


def add_hidden_tool_context(function, arguments, api_key, model, trace_sink=None, approval_callback=None):
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
