import json
import os

from langbridge_cli.parse import extract_output_text, extract_reasoning_summaries, truncate_text


DEFAULT_DEBUG_MAX_CHARS = 200
DEBUG_AGENT_LABELS = {"PM agent", "L4 engineer", "L3 test engineer"}


def llm_debug_enabled():
    return os.environ.get("LANGBRIDGE_DEBUG_LLM", "").strip().lower() in {"1", "true", "yes", "on"}


def debug_max_chars():
    value = os.environ.get("LANGBRIDGE_DEBUG_LLM_MAX_CHARS", "")
    try:
        return max(200, int(value))
    except ValueError:
        return DEFAULT_DEBUG_MAX_CHARS


def limit_debug_line(text):
    max_chars = debug_max_chars()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def print_llm_request(label, model, messages, tool_schemas=None):
    if not should_print_llm_debug(label):
        return

    print(limit_debug_line(f"[LLM DEBUG] {label} input: {format_input(messages)}"))


def print_llm_response(label, response):
    if not should_print_llm_debug(label):
        return

    print(limit_debug_line(f"[LLM DEBUG] {label} output: {format_output(response.get('output', []))}"))


def should_print_llm_debug(label):
    return llm_debug_enabled() and label in DEBUG_AGENT_LABELS


def format_input(messages):
    return " | ".join(format_input_item(index, message) for index, message in enumerate(messages))


def format_output(output):
    return " | ".join(format_output_item(index, item) for index, item in enumerate(output))


def format_input_item(index, message):
    if "role" in message:
        role = message.get("role", "unknown")
        content = truncate_text(message.get("content", ""), debug_max_chars())
        return limit_debug_line(f"{index}. {role}: {content}")
    if message.get("type") == "function_call":
        arguments = truncate_text(message.get("arguments", "{}"), debug_max_chars())
        return limit_debug_line(
            f"{index}. function_call {message.get('name', 'unknown')}"
            f"({arguments}) call_id={message.get('call_id', '')}"
        )
    if message.get("type") == "function_call_output":
        output = truncate_text(message.get("output", ""), debug_max_chars())
        return limit_debug_line(f"{index}. function_call_output call_id={message.get('call_id', '')}: {output}")
    return limit_debug_line(f"{index}. {truncate_text(json.dumps(message, ensure_ascii=False), debug_max_chars())}")


def format_output_item(index, item):
    item_type = item.get("type", "unknown")
    if item_type == "message":
        return limit_debug_line(f"{index}. message: {truncate_text(extract_output_text([item]), debug_max_chars())}")
    if item_type == "function_call":
        arguments = truncate_text(item.get("arguments", "{}"), debug_max_chars())
        return limit_debug_line(
            f"{index}. function_call {item.get('name', 'unknown')}"
            f"({arguments}) call_id={item.get('call_id', '')}"
        )
    if item_type == "function_call_output":
        output = truncate_text(item.get("output", ""), debug_max_chars())
        return limit_debug_line(f"{index}. function_call_output call_id={item.get('call_id', '')}: {output}")
    if item_type == "reasoning":
        summaries = extract_reasoning_summaries([item])
        if summaries:
            return limit_debug_line(f"{index}. reasoning: {truncate_text(' '.join(summaries), debug_max_chars())}")
        return limit_debug_line(f"{index}. reasoning")
    return limit_debug_line(
        f"{index}. {item_type}: {truncate_text(json.dumps(item, ensure_ascii=False), debug_max_chars())}"
    )
