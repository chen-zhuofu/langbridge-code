"""Session-scoped raw traces: traces.md (main agent) + per-agent trace.jsonl."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime

from langbridge_code.context.common.budget import estimate_tokens
from langbridge_code.llm.model_context import model_context_window
from langbridge_code.settings import TRACES_RESUME_MAX_FRACTION
from langbridge_code.util.artifacts import (
    agent_file_prefix,
    traces_dir,
    traces_md_path as artifact_traces_md_path,
)

TRACES_HEADER = "# Session traces\n"
PROGRESS_BOUNDARY_RE = re.compile(
    r"^## Progress boundary \(turn (\d+)\)\s*$",
    re.MULTILINE,
)
_TURN_SECTION_RE = re.compile(r"^## Turn \d+\s*$", re.MULTILINE)

_traces_lock = threading.Lock()


def traces_md_path(run_log_path):
    path = artifact_traces_md_path(run_log_path)
    if path is not None:
        return path
    return run_log_path.with_name("traces.md") if run_log_path else None


def read_traces(run_log_path) -> str:
    if not run_log_path:
        return ""
    path = traces_md_path(run_log_path)
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_traces(run_log_path, content: str) -> None:
    path = traces_md_path(run_log_path)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _filter_round_messages(round_messages: list[dict]) -> list[dict]:
    filtered = []
    for item in round_messages or []:
        if not isinstance(item, dict):
            continue
        if item.get("role") == "system":
            continue
        filtered.append(item)
    return filtered


def _json_block(messages: list[dict]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, indent=2)
    return f"```json\n{payload}\n```"


def append_raw_round(run_log_path, turn_id: int, round_messages: list[dict]) -> None:
    """Append one main-agent raw round (no system) under ``## Turn N``."""
    filtered = _filter_round_messages(round_messages)
    if not run_log_path or not filtered:
        return
    turn = int(turn_id or 0)
    block = _json_block(filtered)
    heading = f"## Turn {turn}"
    with _traces_lock:
        existing = read_traces(run_log_path).strip()
        if not existing or existing == TRACES_HEADER.strip():
            body = TRACES_HEADER + heading + "\n\n" + block + "\n"
            write_traces(run_log_path, body)
            return
        # Append under existing trailing turn heading when it matches.
        last_heading = None
        for match in _TURN_SECTION_RE.finditer(existing):
            last_heading = match
        if last_heading and last_heading.group(0).strip() == heading:
            body = existing.rstrip() + "\n\n" + block + "\n"
        else:
            body = existing.rstrip() + "\n\n" + heading + "\n\n" + block + "\n"
        write_traces(run_log_path, body)


def append_progress_boundary(run_log_path, turn_id: int) -> None:
    """Mark that progress.md now covers traces through this turn."""
    if not run_log_path:
        return
    turn = int(turn_id or 0)
    marker = f"## Progress boundary (turn {turn})"
    with _traces_lock:
        existing = read_traces(run_log_path).strip()
        if not existing or existing == TRACES_HEADER.strip():
            write_traces(run_log_path, TRACES_HEADER + marker + "\n")
            return
        if existing.rstrip().endswith(marker):
            return
        write_traces(run_log_path, existing.rstrip() + "\n\n" + marker + "\n")


_agent_trace_lock = threading.Lock()


def agent_trace_path(run_log_path, label: str, instance_id=None):
    """Per-agent-instance raw trace file: traces/{label}_{id}.trace.jsonl."""
    base = traces_dir(run_log_path)
    if base is None or not label:
        return None
    return base / f"{agent_file_prefix(label, instance_id)}.trace.jsonl"


def append_agent_trace_round(
    run_log_path,
    label: str,
    instance_id,
    turn_id: int,
    round_messages: list[dict],
    *,
    step: int | None = None,
) -> None:
    """Append one raw round (no system) as a JSONL record for this agent."""
    filtered = _filter_round_messages(round_messages)
    path = agent_trace_path(run_log_path, label, instance_id)
    if path is None or not filtered:
        return
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "turn": int(turn_id or 0),
        "messages": filtered,
    }
    if step is not None:
        record["step"] = int(step)
    line = json.dumps(record, ensure_ascii=False)
    with _agent_trace_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_agent_trace(run_log_path, label: str, instance_id=None) -> list[dict]:
    """Parse an agent's trace.jsonl into records (skips corrupt lines)."""
    path = agent_trace_path(run_log_path, label, instance_id)
    if path is None or not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _content_after_last_boundary(content: str) -> str:
    matches = list(PROGRESS_BOUNDARY_RE.finditer(content))
    if not matches:
        return content
    last = matches[-1]
    return content[last.end() :].lstrip("\n")


def _trim_head_to_budget(text: str, max_tokens: int) -> str:
    if max_tokens <= 0 or not text.strip():
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    parts = [part for part in re.split(r"\n{2,}", text.strip()) if part.strip()]
    if not parts:
        return ""
    kept: list[str] = []
    for part in reversed(parts):
        candidate = [part, *kept]
        joined = "\n\n".join(candidate)
        if estimate_tokens(joined) <= max_tokens:
            kept = candidate
            continue
        if not kept:
            approx_chars = max(64, max_tokens * 4)
            return text[-approx_chars:].lstrip()
        break
    return "\n\n".join(kept).strip()


def select_traces_for_resume(run_log_path, *, model: str, progress: str = "") -> str:
    """Return traces text that fits under the resume background budget with progress."""
    content = read_traces(run_log_path).strip()
    if not content or content == TRACES_HEADER.strip():
        return ""
    window = model_context_window(model)
    budget = max(1, int(window * TRACES_RESUME_MAX_FRACTION))
    progress_tokens = estimate_tokens(progress) if progress else 0
    remaining = max(0, budget - progress_tokens)
    if remaining <= 0:
        return ""

    if estimate_tokens(progress + "\n\n" + content) <= budget:
        return content

    after = _content_after_last_boundary(content)
    if after.strip() and estimate_tokens(progress + "\n\n" + after) <= budget:
        return after.strip()

    candidate = after.strip() if after.strip() else content
    return _trim_head_to_budget(candidate, remaining)
