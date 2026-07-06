import json

from langbridge_code.llm.parse import (
    extract_output_text,
    extract_reasoning_items,
    extract_turn_user_input,
    parse_json_string,
)
from langbridge_code.persistence.session import read_session_log


def write_input_log(run_log_path, turn_id, messages):
    upsert_turn_record(
        run_log_path,
        {
            "turn_id": turn_id,
            "user": extract_turn_user_input(messages),
            "input": setup_messages(messages),
            "steps": [],
            "assistant": "",
        },
    )


def setup_messages(messages):
    # Keep only the leading non-user messages (the system prompt). The rest of the
    # turn's input is the prior conversation, which the session log already holds as
    # per-turn user/steps/assistant; storing it again just bloats the file. Resume
    # rebuilds the conversation from those fields plus this system-prompt prefix.
    setup = []
    for message in messages:
        if message.get("role") == "user":
            break
        setup.append(message)
    return setup


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
