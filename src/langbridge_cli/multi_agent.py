import json
import sys

from openai import OpenAI, OpenAIError

from langbridge_cli.config import MAX_SPECIALIST_AGENT_STEPS
from langbridge_cli.debug import print_llm_request, print_llm_response
from langbridge_cli.parse import extract_output_text
from langbridge_cli.roles import L3_TEST_ENGINEER_PROMPT, L4_ENGINEER_PROMPT
from langbridge_cli.tool_schema import strip_tool_purpose, with_tool_purpose
from langbridge_cli.tools import execution, filesystem, testing


L3_TOOL_NAMES = {"list_dir", "find_files", "read_file", "search_files", "run_tests"}
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
    "find_files",
    "read_file",
    "search_files",
    "edit_file",
    "create_file",
    "delete_file",
    "run_tests",
    "execute_program",
}
L4_TOOL_SCHEMAS = with_tool_purpose(
    [
        schema
        for schema in filesystem.TOOL_SCHEMAS + testing.TOOL_SCHEMAS + execution.TOOL_SCHEMAS
        if schema["name"] in L4_TOOL_NAMES
    ]
)
L4_TOOLS = {
    name: tool
    for name, tool in (filesystem.TOOLS | testing.TOOLS | execution.TOOLS).items()
    if name in L4_TOOL_NAMES
}
L4_WRITE_TOOLS = {"create_file", "delete_file", "edit_file"}


def run_l3_test_engineer(api_key, model, task, context=""):
    return run_specialist_agent(
        api_key,
        model,
        L3_TEST_ENGINEER_PROMPT,
        l3_user_prompt(task, context),
        L3_TOOL_SCHEMAS,
        L3_TOOLS,
        "L3 test engineer",
    )


def run_l4_engineer(api_key, model, task, context="", feedback=""):
    return run_specialist_agent(
        api_key,
        model,
        L4_ENGINEER_PROMPT,
        l4_user_prompt(task, context, feedback),
        L4_TOOL_SCHEMAS,
        L4_TOOLS,
        "L4 engineer",
    )


def l3_user_prompt(task, context):
    prompt = f"Task to verify:\n{task}"
    if context:
        prompt += f"\n\nAdditional context from the lead agent:\n{context}"
    return prompt


def l4_user_prompt(task, context, feedback):
    prompt = f"Task to implement:\n{task}"
    if context:
        prompt += f"\n\nAdditional context from the lead agent:\n{context}"
    if feedback:
        prompt += f"\n\nL3 feedback to address:\n{feedback}"
    return prompt


def l3_review_passed(report):
    first_line = report.strip().splitlines()[0].strip().lower() if report.strip() else ""
    return first_line == "review_verdict: pass"


def l4_ready_for_review(report):
    first_line = report.strip().splitlines()[0].strip().lower() if report.strip() else ""
    return first_line == "l4_status: ready_for_review"


def l4_blocked(report):
    first_line = report.strip().splitlines()[0].strip().lower() if report.strip() else ""
    return first_line == "l4_status: blocked"


def run_specialist_agent(api_key, model, system_prompt, user_prompt, tool_schemas, tools, label):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tool_history = []

    for _ in range(MAX_SPECIALIST_AGENT_STEPS):
        response = create_specialist_response(api_key, model, messages, tool_schemas, label)
        output = response.get("output", [])
        tool_calls = [item for item in output if item.get("type") == "function_call"]
        if not tool_calls:
            return extract_output_text(output)

        messages.extend(output)
        for call in tool_calls:
            tool_output = run_specialist_tool_call(call, tools, label)
            tool_history.append({"call": call, "output": tool_output})
            messages.append(tool_output)

    return max_steps_report(label, tool_history)


def create_specialist_response(api_key, model, messages, tool_schemas, label):
    client = OpenAI(api_key=api_key)
    print_llm_request(label, model, messages, tool_schemas)
    try:
        response = client.responses.create(
            model=model,
            input=messages,
            tools=tool_schemas,
            reasoning={"summary": "auto"},
        )
    except OpenAIError as error:
        raise RuntimeError(str(error))

    data = response.model_dump(exclude_none=True)
    print_llm_response(label, data)
    return data


def run_specialist_tool_call(call, tools, label):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = strip_tool_purpose(json.loads(call.get("arguments") or "{}"))
        if name not in tools:
            raise ValueError(f"Unknown {label} tool: {name}")
        if label == "L4 engineer" and name in L4_WRITE_TOOLS and not approve_l4_write_tool(name, arguments):
            raise PermissionError(f"{name} was not approved")
        output = tools[name](**arguments)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def max_steps_report(label, tool_history):
    header = f"{label} stopped because it reached the maximum specialist tool-call steps."
    if label == "L4 engineer":
        header = "L4_STATUS: IN_PROGRESS\nSummary: " + header
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
    return answer.strip().lower() in {"y", "yes"}
