import json
import os

from langbridge_cli.settings import DEFAULT_DEBUG_MAX_CHARS
from langbridge_cli.llm.parse import extract_output_text, truncate_text
from langbridge_cli.llm.tool_schema import TOOL_PURPOSE_ARGUMENT


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
    return


def print_llm_response(label, response):
    if not should_print_llm_debug(label):
        return

    print(limit_debug_line(f"[LLM DEBUG] {label} output: {format_output(response.get('output', []))}"))


def should_print_llm_debug(label):
    return llm_debug_enabled() and label in DEBUG_AGENT_LABELS


def format_output(output):
    items = [
        format_output_item(index, item)
        for index, item in enumerate(output)
        if item.get("type") in {"function_call", "message"}
    ]
    return " | ".join(items)


def format_output_item(index, item):
    item_type = item.get("type", "unknown")
    if item_type == "message":
        return limit_debug_line(f"{index}. message: {truncate_text(extract_output_text([item]), debug_max_chars())}")
    if item_type == "function_call":
        return limit_debug_line(format_function_call(index, item))
    return ""


def format_function_call(index, item):
    arguments = parse_arguments(item.get("arguments") or "{}")
    purpose = ""
    if isinstance(arguments, dict):
        purpose = arguments.pop(TOOL_PURPOSE_ARGUMENT, "")
        rendered_arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    else:
        rendered_arguments = str(arguments)

    call = (
        f"function_call {item.get('name', 'unknown')}"
        f"({truncate_text(rendered_arguments, debug_max_chars())}) call_id={item.get('call_id', '')}"
    )
    if purpose:
        return f"{index}. purpose: {truncate_text(purpose, debug_max_chars())} -> {call}"
    return f"{index}. {call}"


def parse_arguments(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
