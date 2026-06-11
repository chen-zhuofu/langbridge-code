import json
import urllib.request

from langbridge_cli.config import API_URL, MAX_SESSION_SUMMARY_INPUT_CHARS
from langbridge_cli.parse import (
    extract_output_text,
    extract_reasoning_items,
    extract_turn_user_input,
    parse_json_string,
    truncate_text,
)
from langbridge_cli.session import read_session_log


def write_input_log(run_log_path, turn_id, messages):
    upsert_turn_record(
        run_log_path,
        {
            "turn_id": turn_id,
            "user": extract_turn_user_input(messages),
            "input": messages,
            "steps": [],
            "assistant": "",
        },
    )


def write_tool_calls_log(run_log_path, turn_id, step, step_response):
    record = read_turn_record(run_log_path, turn_id)
    record["steps"].append(format_log_step(step, step_response))
    upsert_turn_record(run_log_path, record)


def write_tool_calls_result_log(run_log_path, turn_id, step, tool_output):
    record = read_turn_record(run_log_path, turn_id)
    record["steps"][step]["observation"].extend(format_observations([tool_output]))
    upsert_turn_record(run_log_path, record)


def write_finish_log(run_log_path, turn_id, finished):
    record = read_turn_record(run_log_path, turn_id)
    record["assistant"] = finished
    upsert_turn_record(run_log_path, record)


def read_turn_record(run_log_path, turn_id):
    if not run_log_path.exists():
        return None
    session_log = read_session_log(run_log_path)
    for record in session_log["turns"]:
        if record.get("turn_id") == turn_id:
            return record
    return None


def upsert_turn_record(run_log_path, record):
    session_log = {"summary": "", "turns": []}
    if run_log_path.exists():
        session_log = read_session_log(run_log_path)

    for index, existing_record in enumerate(session_log["turns"]):
        if existing_record.get("turn_id") == record["turn_id"]:
            session_log["turns"][index] = record
            break
    else:
        session_log["turns"].append(record)

    run_log_path.write_text(
        json.dumps(session_log, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_session_summary(api_key, model, run_log_path):
    session_log = read_session_log(run_log_path)
    if session_log["summary"]:
        return

    session_log["summary"] = create_session_summary(api_key, model, session_log["turns"])
    run_log_path.write_text(
        json.dumps(session_log, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_session_summary(api_key, model, records):
    prompt = (
        "Summarize this coding-agent CLI session as a short title for a session picker. "
        "Return only the title, no punctuation wrapper, under 12 words.\n\n"
        f"{session_summary_input(records)}"
    )
    data = create_text_response(
        api_key,
        model,
        [
            {"role": "system", "content": "You write concise session titles."},
            {"role": "user", "content": prompt},
        ],
    )
    return extract_output_text(data.get("output", [])).strip()


def create_text_response(api_key, model, agent_input):
    body = json.dumps({"model": model, "input": agent_input}).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def session_summary_input(records):
    lines = []
    for record in records[-5:]:
        user = record.get("user")
        assistant = record.get("assistant")
        if user:
            lines.append(f"User: {truncate_text(user, 300)}")
        if assistant:
            lines.append(f"Assistant: {truncate_text(assistant, 300)}")

    return truncate_text("\n".join(lines), MAX_SESSION_SUMMARY_INPUT_CHARS)


def format_log_step(step, output):
    return {
        "step": step,
        "reasoning": extract_reasoning_items(output),
        "action": format_actions(output),
        "observation": format_observations(output),
    }


def format_actions(output):
    actions = []
    for item in output:
        if item.get("type") == "function_call":
            actions.append(
                {
                    "call_id": item.get("call_id"),
                    "name": item.get("name"),
                    "arguments": parse_json_string(item.get("arguments") or "{}"),
                }
            )
        elif item.get("type") == "message":
            actions.append({"type": "message", "content": extract_output_text([item])})
    return actions


def format_observations(output):
    return [
        {
            "call_id": item.get("call_id"),
            "output": item.get("output", ""),
        }
        for item in output
        if item.get("type") == "function_call_output"
    ]
