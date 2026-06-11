import copy

from langbridge_cli.trace import (
    extract_output_text,
    extract_reasoning_summaries,
    extract_trace_events,
    parse_json_string,
)


DIM = "\033[2m"
RESET = "\033[0m"


def extract_reasoning_items(output):
    return [copy.deepcopy(item) for item in output if item.get("type") == "reasoning"]


def truncate_text(text, max_chars):
    compact = " ".join(str(text).split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + "..."


def print_step_trace(output, include_message=False, label="Agent", sink=None):
    events = extract_trace_events(output, label=label, include_message=include_message)
    for event in events:
        if sink is not None:
            sink(event)
            continue
        prefix = "\n" if event.kind == "thought" else ""
        marker = "↳ " if event.kind == "action" else ""
        print(f"{prefix}{dim_text(f'{event.role}: {marker}{event.text}')}")


def dim_text(text):
    return f"{DIM}{text}{RESET}"


def extract_turn_user_input(agent_input):
    for message in reversed(agent_input):
        if message.get("role") == "user":
            return message["content"]
    raise ValueError("agent_input has no user message")

