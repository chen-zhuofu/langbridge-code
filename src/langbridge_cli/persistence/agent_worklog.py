"""Per-agent worklog: each role's own always-on trace of what it did this loop.

For every agent (PM, L3, L4, L5) we append a human-readable record of its
reasoning, the tool calls it made (action), and what came back (observation),
plus its final report. It is an audit/debug record, never read back by an agent.

This is distinct from the two other records:
  - the shared worker<->L3 negotiation ledger (persistence/worklog.py), and
  - the user<->PM session history (persistence/session.py).
"""

import json

from langbridge_cli import config
from langbridge_cli.llm.parse import extract_output_text, extract_reasoning_summaries
from langbridge_cli.llm.tool_schema import TOOL_PURPOSE_ARGUMENT


_WORKLOG_FILE_BY_LABEL = {
    "PM agent": ("PM_WORKLOG_DIR", "pm_worklog.md"),
    "L3 test engineer": ("L3_WORKLOG_DIR", "l3_worklog.md"),
    "L4 engineer": ("L4_WORKLOG_DIR", "l4_worklog.md"),
    "L5 engineer": ("L5_WORKLOG_DIR", "l5_worklog.md"),
}


def worklog_path(run_log_path, label):
    # No active run (e.g. unit tests passing run_log_path=None) -> no-op, so the writer
    # never litters when there is no real loop in flight.
    if run_log_path is None:
        return None
    entry = _WORKLOG_FILE_BY_LABEL.get(label)
    if entry is None:
        return None
    dir_attr, name = entry
    return getattr(config, dir_attr) / name


def write_worklog_step(run_log_path, label, turn_id, step, output):
    path = worklog_path(run_log_path, label)
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


def write_worklog_observation(run_log_path, label, turn_id, step, tool_output):
    path = worklog_path(run_log_path, label)
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


def write_worklog_finish(run_log_path, label, turn_id, finished):
    path = worklog_path(run_log_path, label)
    if path is None:
        return

    _append(path, [f"### [{label}] turn {turn_id} · FINAL", "", finished, ""])


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
