import json

from langbridge_cli.debug import llm_debug_enabled
from langbridge_cli.parse import extract_output_text, extract_reasoning_summaries
from langbridge_cli.tool_schema import TOOL_PURPOSE_ARGUMENT


def trajectory_enabled():
    return llm_debug_enabled()


def trajectory_path(run_log_path):
    if run_log_path is None:
        return None
    return run_log_path.with_name(f"{run_log_path.stem}.trajectory.md")


def write_trajectory_step(run_log_path, label, turn_id, step, output):
    path = _active_path(run_log_path)
    if path is None:
        return

    lines = [f"### [{label}] turn {turn_id} · step {step}", ""]
    summaries = extract_reasoning_summaries(output)
    if summaries:
        lines.append("**reasoning:**")
        lines.extend(f"- {text}" for text in summaries)
        lines.append("")
    actions = _render_actions(output)
    if actions:
        lines.append("**action:**")
        lines.extend(actions)
        lines.append("")
    _append(path, lines)


def write_trajectory_observation(run_log_path, label, turn_id, step, tool_output):
    path = _active_path(run_log_path)
    if path is None:
        return

    lines = [
        f"**observation** (call_id={tool_output.get('call_id', '')}):",
        "",
        "```",
        str(tool_output.get("output", "")),
        "```",
        "",
    ]
    _append(path, lines)


def write_trajectory_finish(run_log_path, label, turn_id, finished):
    path = _active_path(run_log_path)
    if path is None:
        return

    _append(path, [f"### [{label}] turn {turn_id} · FINAL", "", finished, ""])


def _active_path(run_log_path):
    if not trajectory_enabled():
        return None
    return trajectory_path(run_log_path)


def _render_actions(output):
    lines = []
    for item in output:
        item_type = item.get("type")
        if item_type == "message":
            text = extract_output_text([item])
            if text:
                lines.append(f"- message: {text}")
        elif item_type == "function_call":
            lines.append(_render_function_call(item))
    return lines


def _render_function_call(item):
    name = item.get("name", "unknown")
    try:
        arguments = json.loads(item.get("arguments") or "{}")
    except json.JSONDecodeError:
        arguments = item.get("arguments")

    purpose = ""
    if isinstance(arguments, dict):
        purpose = arguments.pop(TOOL_PURPOSE_ARGUMENT, "")
        rendered = json.dumps(arguments, ensure_ascii=False)
    else:
        rendered = str(arguments)

    if purpose:
        return f"- {name}({rendered})  · purpose: {purpose}"
    return f"- {name}({rendered})"


def _append(path, lines):
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
