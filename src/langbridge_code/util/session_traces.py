"""Session-scoped raw traces: traces.md (main agent, read on cold-start resume)."""
from __future__ import annotations

import json
import re
import threading

from langbridge_code.context.common.budget import estimate_tokens
from langbridge_code.llm.model_context import model_context_window
from langbridge_code.settings import TRACES_RESUME_MAX_FRACTION
from langbridge_code.util.artifacts import traces_md_path as artifact_traces_md_path

TRACES_HEADER = "# Session traces\n"
PROGRESS_BOUNDARY_RE = re.compile(
    r"^## Progress boundary \(turn (\d+)\)\s*$",
    re.MULTILINE,
)
_TURN_SECTION_RE = re.compile(r"^## Turn \d+\s*$", re.MULTILINE)
_TURN_ID_RE = re.compile(r"^## Turn (\d+)\s*$", re.MULTILINE)
_JSON_BLOCK_RE = re.compile(r"^```json\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)

_traces_lock = threading.Lock()


def traces_md_path(run_log_path):
    return artifact_traces_md_path(run_log_path)


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


def _message_text(item: dict) -> str:
    """Plain text of a user/assistant message (string or content-part list)."""
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


# Engine-injected user messages (hooks, status blocks, pinned background);
# not part of the human conversation, so hidden from resume replay.
_INJECTED_PREFIXES = (
    "[HOOK]",
    "[CONTEXT_STATUS]",
    "<background>",
    "<background_tool_results>",
    "<assigned_task>",
)


def read_conversation(run_log_path) -> list[tuple[str, str]]:
    """Ordered (role, text) user/assistant messages from the raw session traces.

    Used by the TUI to replay the full past conversation on resume. Tool
    calls, tool outputs, reasoning items, and engine-injected user messages
    (progress-note hooks, context status, background blocks) are skipped.
    """
    content = read_traces(run_log_path)
    conversation: list[tuple[str, str]] = []
    for match in _JSON_BLOCK_RE.finditer(content):
        try:
            items = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _message_text(item).strip()
            if not text:
                continue
            if role == "user" and text.startswith(_INJECTED_PREFIXES):
                continue
            conversation.append((role, text))
    return conversation


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


def last_traces_turn_id(run_log_path) -> int:
    """Highest turn id recorded in traces.md (0 if none)."""
    content = read_traces(run_log_path)
    ids = [int(match.group(1)) for match in _TURN_ID_RE.finditer(content)]
    ids.extend(int(match.group(1)) for match in PROGRESS_BOUNDARY_RE.finditer(content))
    return max(ids, default=0)


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


def build_resume_background(run_log_path, *, model: str, progress: str = "") -> str:
    """Background text for a cold start (new session object / resume).

    When the full raw traces fit the resume budget on their own, use them
    directly — the progress notes are just a summary of the same rounds.
    Otherwise fall back to progress notes plus the traces recorded after the
    last progress boundary (rounds not yet summarized into progress.md),
    trimmed from the head when even those exceed the remaining budget.
    """
    progress = (progress or "").strip()
    content = read_traces(run_log_path).strip()
    if not content or content == TRACES_HEADER.strip():
        return progress

    window = model_context_window(model)
    budget = max(1, int(window * TRACES_RESUME_MAX_FRACTION))
    if estimate_tokens(content) <= budget:
        return content

    # Rounds after the last boundary are the only ones progress.md does not
    # cover; a healthy shutdown leaves nothing here and progress alone suffices.
    after = _content_after_last_boundary(content).strip()
    if not after:
        return progress
    remaining = max(0, budget - estimate_tokens(progress))
    tail = _trim_head_to_budget(after, remaining)
    if not tail.strip():
        return progress
    heading = "## Raw main-agent traces since the last progress note"
    return (progress + "\n\n" if progress else "") + heading + "\n\n" + tail.strip()
