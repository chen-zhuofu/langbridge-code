import copy
import json
import sys
import urllib.error
import urllib.request

from langbridge_cli.config import API_URL, MAX_AGENT_STEPS, WRITE_TOOLS
from langbridge_cli.logging import (
    write_finish_log,
    write_input_log,
    write_tool_calls_log,
    write_tool_calls_result_log,
)
from langbridge_cli.parse import extract_output_text, print_step_trace
from langbridge_cli.tools import TOOL_SCHEMAS, TOOLS


def run_agent(api_key, model, input, run_log_path, turn_id):
    write_input_log(run_log_path, turn_id, input) # write current message into log
    for step in range(MAX_AGENT_STEPS):
        step_response = create_response(api_key, model, input).get("output", [])
        tool_calls = [item for item in step_response if item.get("type") == "function_call"]
        print_step_trace(step_response, include_message=bool(tool_calls))

        if tool_calls:
            input.extend(step_response)
            write_tool_calls_log(run_log_path, turn_id, step, step_response) # write step_response or socalled "action" into log
            for call in tool_calls:
                tool_output = run_tool_call(call)
                input.append(tool_output)
                write_tool_calls_result_log(run_log_path, turn_id, step, tool_output) # write tool_output or socalled "observation" into log
        else:
            finished = extract_output_text(step_response)
            input.append({"role": "assistant", "content": finished})
            write_finish_log(run_log_path, turn_id, finished) # write finished or socalled "agent loop ouput" into log
            print(f"\n{finished}\n")
            return
    finished = "Agent stopped because it reached the maximum tool-call steps."
    input.append({"role": "assistant", "content": finished})
    write_finish_log(run_log_path, turn_id, finished) #write finished or socalled "agent loop ouput" into log
    print(f"\n{finished}\n")


def create_response(api_key, model, agent_input):
    body = json.dumps(
        {
            "model": model,
            "input": agent_input,
            "tools": TOOL_SCHEMAS,
            "reasoning": {"summary": "auto"},
        }
    ).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        data = json.loads(error.read())
        raise RuntimeError(data.get("error", {}).get("message", "OpenAI request failed"))

    return data


def run_tool_call(call):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = json.loads(call.get("arguments") or "{}")
        if name not in TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        if name in WRITE_TOOLS and not approve_write_tool(name, arguments):
            raise PermissionError(f"{name} was not approved")
        output = TOOLS[name](**arguments)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def approve_write_tool(name, arguments):
    if not sys.stdin.isatty():
        return False

    print(f"\nApprove write tool: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    answer = input("Run this tool? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}
