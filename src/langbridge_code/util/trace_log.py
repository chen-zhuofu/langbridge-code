"""Unified session trace log: one markdown file per session (session.md).

Each turn appends a `## <trace_id>` heading followed by one entry per event,
written in completion order. Entries carry full text — nothing is truncated.
Oversized payloads (huge tool outputs etc.) are stored as separate files under
attachments/ with a markdown link in the entry."""
from __future__ import annotations

import itertools
import json
import threading
from dataclasses import dataclass

from langbridge_code.util.artifacts import (
    ATTACHMENTS_DIRNAME,
    attachments_dir,
    format_line_timestamp,
    session_trace_path,
)

_TRACE_LOG_HEADER = "# Session trace log\n"

# Entries longer than this go to a linked attachment file instead of inline.
_MAX_INLINE_CHARS = 4_000
# Inline preview kept in the log when the full text moves to an attachment.
_ATTACHMENT_PREVIEW_CHARS = 200

_attachment_seq = itertools.count(1)

_TOOL_DESCRIPTION = "description"

_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()


@dataclass
class TraceContext:
    run_log_path: object
    trace_id: str


_current = threading.local()


def set_trace_context(ctx: TraceContext | None) -> None:
    _current.ctx = ctx


def get_trace_context() -> TraceContext | None:
    return getattr(_current, "ctx", None)


def begin_trace(run_log_path, trace_id: str) -> TraceContext:
    ctx = TraceContext(run_log_path=run_log_path, trace_id=trace_id)
    set_trace_context(ctx)
    path = session_trace_path(run_log_path)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = _lock_for(str(path))
        with lock:
            if not path.exists():
                path.write_text(_TRACE_LOG_HEADER, encoding="utf-8")
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n## {trace_id}\n\n")
    return ctx


def end_trace() -> None:
    set_trace_context(None)


def _lock_for(path: str) -> threading.Lock:
    with _LOCK_GUARD:
        lock = _LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[path] = lock
        return lock


def _append_line(run_log_path, trace_id: str, line: str) -> None:
    path = session_trace_path(run_log_path)
    if path is None or not trace_id:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(str(path))
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")


def _save_attachment(run_log_path, trace_id: str, text: str) -> str | None:
    """Store oversized text under attachments/, return the relative link."""
    directory = attachments_dir(run_log_path)
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{trace_id}-{next(_attachment_seq):03d}.txt"
    (directory / name).write_text(text, encoding="utf-8")
    return f"{ATTACHMENTS_DIRNAME}/{name}"


def _format_entry(run_log_path, trace_id: str, agent: str, text: str) -> str:
    """Full-text entry; huge payloads move to a linked attachment file."""
    text = (text or "").rstrip()
    if len(text) > _MAX_INLINE_CHARS:
        link = _save_attachment(run_log_path, trace_id, text)
        preview = " ".join(text[:_ATTACHMENT_PREVIEW_CHARS].split())
        if link:
            text = f"{preview}... [full text]({link})"
        else:
            text = preview + "..."
    stamp = format_line_timestamp()
    agent_name = (agent or "Agent").strip()
    lines = text.splitlines() or [""]
    entry = f"{stamp} · {agent_name} · {lines[0]}"
    # Indent continuation lines so an entry can never open a new `## ` section.
    for line in lines[1:]:
        entry += "\n  " + line
    return entry


def write_line(agent: str, detail: str, *, run_log_path=None, trace_id: str | None = None) -> None:
    ctx = get_trace_context()
    run = run_log_path or (ctx.run_log_path if ctx else None)
    tid = trace_id or (ctx.trace_id if ctx else None)
    if run is None or not tid:
        return
    _append_line(run, tid, _format_entry(run, tid, agent, str(detail)))


def trace_sink(event) -> None:
    role = getattr(event, "role", "Agent")
    kind = getattr(event, "kind", "event")
    if kind.endswith("_stream"):
        return
    text = getattr(event, "text", "")
    if kind == "action":
        write_line(role, f"→ {text}")
    elif kind == "reasoning":
        write_line(role, f"think: {text}")
    else:
        write_line(role, text)


def combine_trace_sink(*sinks):
    callbacks = [sink for sink in sinks if sink is not None]

    def combined(event):
        for callback in callbacks:
            callback(event)

    return combined if callbacks else None


def log_received(agent: str, text: str) -> None:
    write_line(agent, f"input: {(text or '').strip()}")


def log_finish(agent: str, text: str) -> None:
    write_line(agent, f"done: {(text or '').strip()}")


def log_tool_result(agent: str, tool_name: str, output: str) -> None:
    write_line(agent, f"← {tool_name}: {str(output or '').strip()}")


def log_from_step_output(agent: str, output) -> None:
    from langbridge_code.llm.trace import extract_output_text, extract_reasoning_summaries

    for summary in extract_reasoning_summaries(output):
        write_line(agent, f"think: {summary}")
    for item in output:
        if item.get("type") == "function_call":
            write_line(agent, f"→ {_format_tool_call(item)}")
        elif item.get("type") == "message":
            text = extract_output_text([item]).strip()
            if text:
                write_line(agent, text)


def _trace_sections(run_log_path) -> list[tuple[str, list[str]]]:
    """[(trace_id, lines), ...] parsed from the session trace log, oldest first."""
    path = session_trace_path(run_log_path)
    if path is None or not path.is_file():
        return []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = []
            sections.append((line[3:].strip(), current))
        elif current is not None and line.strip():
            current.append(line)
    return sections



def read_latest_trace_for_turn(run_log_path, turn_id: int) -> list[str]:
    del turn_id
    sections = _trace_sections(run_log_path)
    if not sections:
        return []
    return sections[-1][1]


def _format_tool_call(item) -> str:
    name = item.get("name", "tool")
    try:
        arguments = json.loads(item.get("arguments") or "{}")
    except json.JSONDecodeError:
        return name
    if isinstance(arguments, dict):
        arguments = {key: val for key, val in arguments.items() if key != _TOOL_DESCRIPTION}
        rendered = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        return f"{name}({rendered})"
    return name
