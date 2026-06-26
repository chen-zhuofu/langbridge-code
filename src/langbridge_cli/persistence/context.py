import json

from langbridge_cli.config import (
    COMPACT_WHEN_TOKENS_OVER,
    MAX_TOOL_SUMMARY_OUTPUT_CHARS,
    RECENT_CONTEXT_TOKENS,
    STALE_TOOL_OUTPUT_CHARS,
    SUMMARY_TARGET_CHARS,
)
from langbridge_cli.llm.parse import truncate_text


def restore_session_messages(records):
    if not records:
        return []

    messages = restore_full_session_messages(records)
    if estimate_tokens(messages) <= COMPACT_WHEN_TOKENS_OVER:
        return messages

    return restore_compacted_session_messages(records)


def restore_full_session_messages(records):
    messages = initial_messages_from_record(records[0])
    for record in records:
        user = record.get("user")
        if user:
            messages.append({"role": "user", "content": user})
        append_turn_messages(messages, record.get("steps", []), record.get("assistant", ""))
    return messages


def initial_messages_from_record(record):
    messages = clone(record.get("input") or record.get("agent_input") or [])
    initial = []
    for message in messages:
        if message.get("role") == "user":
            break
        initial.append(message)
    return initial


def restore_compacted_session_messages(records):
    initial = initial_messages_from_record(records[0])
    recent_records = select_recent_records(records)
    older_records = records[: len(records) - len(recent_records)]

    messages = list(initial)
    summary = summarize_old_records(older_records)
    if summary:
        messages.append({"role": "assistant", "content": summary})

    for index, record in enumerate(recent_records):
        user = record.get("user")
        if user:
            messages.append({"role": "user", "content": user})
        turn_messages = []
        append_turn_messages(turn_messages, record.get("steps", []), record.get("assistant", ""))
        if index < len(recent_records) - 2:
            truncate_stale_tool_outputs(turn_messages)
        messages.extend(turn_messages)
    return messages


def truncate_stale_tool_outputs(items):
    for item in items:
        if item.get("type") == "function_call_output":
            item["output"] = truncate_text(item.get("output", ""), STALE_TOOL_OUTPUT_CHARS)


def select_recent_records(records):
    selected = []
    for record in reversed(records):
        candidate = [record] + selected
        if selected and estimate_tokens(records_to_messages(candidate)) > RECENT_CONTEXT_TOKENS:
            break
        selected = candidate
    return selected or records[-1:]


def records_to_messages(records):
    messages = []
    for record in records:
        user = record.get("user")
        if user:
            messages.append({"role": "user", "content": user})
        append_turn_messages(messages, record.get("steps", []), record.get("assistant", ""))
    return messages


def summarize_old_records(records):
    if not records:
        return ""

    lines = ["Older session summary:"]
    for record in records:
        user = record.get("user")
        assistant = record.get("assistant")
        if user:
            lines.append(f"- User: {truncate_text(user, 300)}")
        tool_activity = summarize_record_tool_activity(record)
        if tool_activity:
            lines.append(f"  Tools: {tool_activity}")
        if assistant:
            lines.append(f"  Assistant: {truncate_text(assistant, 300)}")
    return truncate_text("\n".join(lines), SUMMARY_TARGET_CHARS)


def summarize_record_tool_activity(record):
    lines = []
    for step in record.get("steps", []):
        for item in step.get("action", []):
            if item.get("name"):
                lines.append(f"{item['name']}({json.dumps(item.get('arguments', {}), ensure_ascii=False)})")
        if "output" in step:
            for item in step["output"]:
                if item.get("type") == "function_call":
                    lines.append(f"{item.get('name', 'unknown')}({item.get('arguments', '{}')})")
    return truncate_text("; ".join(lines), 500)


def estimate_tokens(value):
    return len(json.dumps(value, ensure_ascii=False)) // 4


def clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def append_turn_messages(messages, steps, assistant_reply):
    messages.extend(tool_items_from_steps(steps))
    if assistant_reply:
        messages.append({"role": "assistant", "content": assistant_reply})


def tool_items_from_steps(steps):
    items = []
    for step in steps:
        if "output" in step:
            items.extend(tool_items_from_output(step["output"]))
        else:
            items.extend(tool_items_from_formatted_step(step))
    return items


def tool_items_from_output(output):
    return [
        clone(item)
        for item in output
        if item.get("type") in {"reasoning", "function_call", "function_call_output"}
    ]


def tool_items_from_formatted_step(step):
    reasoning = step.get("reasoning", [])
    if not reasoning and step.get("action"):
        return previous_tool_activity_message(step)

    items = []
    items.extend(clone(reasoning))
    action = step.get("action", [])
    if isinstance(action, dict):
        action = action.get("tool_calls", [])

    for item in action:
        if item.get("name") and item.get("call_id"):
            items.append(
                {
                    "type": "function_call",
                    "call_id": item["call_id"],
                    "name": item["name"],
                    "arguments": json.dumps(item.get("arguments", {}), ensure_ascii=False),
                }
            )

    for item in step.get("observation", []):
        if item.get("call_id"):
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": item["call_id"],
                    "output": item.get("output", ""),
                }
            )
    return items


def previous_tool_activity_message(step):
    lines = []
    observations = {
        item.get("call_id"): item.get("output", "")
        for item in step.get("observation", [])
        if item.get("call_id")
    }
    action = step.get("action", [])
    if isinstance(action, dict):
        action = action.get("tool_calls", [])

    for item in action:
        name = item.get("name")
        call_id = item.get("call_id")
        if not name:
            continue
        arguments = json.dumps(item.get("arguments", {}), ensure_ascii=False)
        output = truncate_text(observations.get(call_id, ""), MAX_TOOL_SUMMARY_OUTPUT_CHARS)
        lines.append(f"- {name}({arguments}): {output}")

    if not lines:
        return []
    return [{"role": "assistant", "content": "Previous tool activity:\n" + "\n".join(lines)}]
