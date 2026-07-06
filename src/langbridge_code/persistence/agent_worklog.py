"""Per-agent worklog: each agent instance's own always-on trace of what it did.

For every agent (PM, L3, L4, L5) we append a human-readable record of its
reasoning, the tool calls it made (action), and what came back (observation),
plus what it received and its final report. It is an audit/debug record, never
read back by an agent.

Each distinct agent instance gets its OWN file, so traces never pile together:
one L4<->L3 review can spin up several L3s (the main reviewer, plus fresh jurors)
and each PM round is a fresh, memoryless PM -- every one writes to a separate
file. Files are grouped per run: agent-state/<role>/worklog/<run>/<role>_<n>.md.

This is distinct from:
  - the optimizer trace JSONL (workflow/optimizer_trace.py), and
  - the user session history (persistence/session.py).
"""

import json

from langbridge_code import settings
from langbridge_code.llm.parse import extract_output_text, extract_reasoning_summaries
from langbridge_code.llm.tool_schema import TOOL_PURPOSE_ARGUMENT


# label -> (config dir attribute, file-name prefix)
_WORKLOG_FILE_BY_LABEL = {
    "PM agent": ("PM_WORKLOG_DIR", "pm"),
    "Planner": ("PLANNER_WORKLOG_DIR", "planner"),
    "Presenter": ("PRESENTER_WORKLOG_DIR", "presenter"),
    "Coder": ("CODER_WORKLOG_DIR", "coder"),
    "Reviewer": ("REVIEWER_WORKLOG_DIR", "reviewer"),
    # Legacy labels (training / compat)
    "L3 test engineer": ("REVIEWER_WORKLOG_DIR", "reviewer"),
    "L4 engineer": ("CODER_WORKLOG_DIR", "coder"),
    "L5 engineer": ("CODER_WORKLOG_DIR", "coder"),
}

# (run, label) -> count of instances handed out so far. Resets naturally per run
# because each run uses a fresh run_log_path, so instance numbers restart at 1.
_INSTANCE_COUNTERS = {}


def new_worklog_id(run_log_path, label):
    """Reserve a fresh per-instance worklog id (1-based) for one new agent.

    Call this once when an agent instance is created; pass the returned id to
    every write_worklog_* call for that instance so they all land in one file.
    Returns None when there is no active run (e.g. unit tests), making the
    writers no-ops.
    """
    if run_log_path is None or label not in _WORKLOG_FILE_BY_LABEL:
        return None
    key = (str(run_log_path), label)
    next_id = _INSTANCE_COUNTERS.get(key, 0) + 1
    _INSTANCE_COUNTERS[key] = next_id
    return next_id


def worklog_path(run_log_path, label, instance_id=None):
    # No active run (e.g. unit tests passing run_log_path=None) -> no-op, so the writer
    # never litters when there is no real loop in flight.
    if run_log_path is None:
        return None
    entry = _WORKLOG_FILE_BY_LABEL.get(label)
    if entry is None:
        return None
    dir_attr, prefix = entry
    run_dir = getattr(settings, dir_attr) / run_log_path.stem
    name = f"{prefix}.md" if instance_id is None else f"{prefix}_{instance_id}.md"
    return run_dir / name


def write_worklog_received(run_log_path, label, instance_id, turn_id, text):
    # The incoming message this agent was handed (the user task for the PM, or the
    # other side's report/feedback for a specialist). Logged so each worklog reads
    # as a full exchange — what it received, then what it did about it — not just
    # the agent's own half.
    path = worklog_path(run_log_path, label, instance_id)
    if path is None:
        return
    _append(path, [f"### [{label}] turn {turn_id} \u00b7 RECEIVED", "", text.strip(), ""])


def write_worklog_step(run_log_path, label, instance_id, turn_id, step, output):
    path = worklog_path(run_log_path, label, instance_id)
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


def write_worklog_observation(run_log_path, label, instance_id, turn_id, step, tool_output):
    path = worklog_path(run_log_path, label, instance_id)
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


def write_worklog_finish(run_log_path, label, instance_id, turn_id, finished):
    path = worklog_path(run_log_path, label, instance_id)
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
