import json
from dataclasses import dataclass

from langbridge_cli.llm.tool_schema import TOOL_PURPOSE_ARGUMENT


@dataclass(frozen=True)
class ThoughtEvent:
    role: str
    kind: str
    text: str
    tool_name: str = ""
    arguments: str = ""


def extract_trace_events(output, label="Agent", include_message=False):
    thought_events = []
    action_events = []
    for item in output:
        if item.get("type") != "function_call":
            continue

        purpose = extract_tool_purpose(item)
        if purpose:
            thought_events.append(ThoughtEvent(role=label, kind="thought", text=purpose))
        action_events.append(
            ThoughtEvent(
                role=label,
                kind="action",
                text=f"{item.get('name', 'unknown')}({format_tool_arguments(item)})",
                tool_name=item.get("name", "unknown"),
                arguments=format_tool_arguments(item),
            )
        )

    if action_events:
        if not thought_events and include_message:
            message = extract_output_text(output)
            if message:
                thought_events.append(ThoughtEvent(role=label, kind="thought", text=message))
        return thought_events + action_events

    if include_message:
        message = extract_output_text(output)
        if message:
            return [ThoughtEvent(role=label, kind="thought", text=message)]

    return [
        ThoughtEvent(role=label, kind="thought", text=summary)
        for summary in extract_reasoning_summaries(output)
    ]


def extract_tool_purpose(item):
    arguments = parse_json_string(item.get("arguments") or "{}")
    if isinstance(arguments, dict):
        return arguments.get(TOOL_PURPOSE_ARGUMENT, "")
    return ""


def format_tool_arguments(item):
    arguments = parse_json_string(item.get("arguments") or "{}")
    if isinstance(arguments, dict):
        arguments.pop(TOOL_PURPOSE_ARGUMENT, None)
        return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    return item.get("arguments") or "{}"


def parse_json_string(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def extract_reasoning_summaries(output):
    return [
        content["text"]
        for item in output
        if item.get("type") == "reasoning"
        for content in item.get("summary", [])
        if content.get("type") == "summary_text" and content.get("text")
    ]


def extract_output_text(output):
    return "".join(
        content["text"]
        for item in output
        for content in item.get("content", [])
        if content["type"] == "output_text"
    )
