"""Unified per-turn trace log: one line per event, written in completion order."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from langbridge_code.util.artifacts import format_line_timestamp, traces_dir

_TOOL_PURPOSE = "purpose"

_MAX_DETAIL_CHARS = 80
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
    base = traces_dir(run_log_path)
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{trace_id}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")
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
    base = traces_dir(run_log_path)
    if base is None or not trace_id:
        return
    path = base / f"{trace_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(str(path))
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")


def write_line(agent: str, detail: str, *, run_log_path=None, trace_id: str | None = None) -> None:
    ctx = get_trace_context()
    run = run_log_path or (ctx.run_log_path if ctx else None)
    tid = trace_id or (ctx.trace_id if ctx else None)
    if run is None or not tid:
        return
    agent_name = (agent or "Agent").strip()
    text = _truncate(detail)
    stamp = format_line_timestamp()
    _append_line(run, tid, f"{stamp} · {agent_name} · {text}")


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
    preview = _truncate(" ".join((text or "").split()), 120)
    write_line(agent, f"input: {preview}")


def log_finish(agent: str, text: str) -> None:
    preview = _truncate(" ".join((text or "").split()), 120)
    write_line(agent, f"done: {preview}")


def log_tool_result(agent: str, tool_name: str, output: str) -> None:
    write_line(agent, f"← {tool_name}: {_summarize_tool_output(tool_name, output)}")


def log_from_step_output(agent: str, output) -> None:
    from langbridge_code.llm.trace import extract_output_text, extract_reasoning_summaries

    for summary in extract_reasoning_summaries(output):
        write_line(agent, f"think: {_truncate(summary)}")
    for item in output:
        if item.get("type") == "function_call":
            write_line(agent, f"→ {_format_tool_call(item)}")
        elif item.get("type") == "message":
            text = extract_output_text([item]).strip()
            if text:
                write_line(agent, _truncate(text))


def read_trace_lines(run_log_path, trace_id: str) -> list[str]:
    base = traces_dir(run_log_path)
    if base is None:
        return []
    path = base / f"{trace_id}.log"
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_latest_trace_for_turn(run_log_path, turn_id: int) -> list[str]:
    del turn_id
    base = traces_dir(run_log_path)
    if base is None or not base.is_dir():
        return []
    logs = sorted(base.glob("*.log"), key=lambda path: path.stat().st_mtime)
    if not logs:
        return []
    return [line for line in logs[-1].read_text(encoding="utf-8").splitlines() if line.strip()]


def _format_tool_call(item) -> str:
    name = item.get("name", "tool")
    try:
        arguments = json.loads(item.get("arguments") or "{}")
    except json.JSONDecodeError:
        return name
    if isinstance(arguments, dict):
        arguments = {key: val for key, val in arguments.items() if key != _TOOL_PURPOSE}
        return _summarize_tool_call(name, arguments)
    return name


def _summarize_tool_call(name: str, arguments: dict) -> str:
    if name == "read_file":
        return f"read_file({arguments.get('path', '?')})"
    if name == "grep":
        return f"grep({arguments.get('pattern', '?')})"
    if name == "glob":
        return f"glob({arguments.get('pattern', '?')})"
    if name == "list_dir":
        return f"list_dir({arguments.get('path', '.')})"
    if name == "bash":
        cmd = str(arguments.get("command", ""))
        return f"bash({_truncate(cmd, 50)})"
    if name == "agent_worker":
        return f"agent_worker({_truncate(str(arguments.get('prompt', '')), 40)})"
    if name == "agent_planner":
        return f"agent_planner({_truncate(str(arguments.get('prompt', '')), 40)})"
    if name == "agent_explorer":
        return f"agent_explorer({_truncate(str(arguments.get('prompt', '')), 40)})"
    if name == "write":
        return f"write({arguments.get('path', '?')})"
    if name == "edit_file":
        return f"edit_file({arguments.get('path', '?')})"
    if name == "multi_edit":
        return f"multi_edit({arguments.get('path', '?')})"
    if name == "apply_patch":
        return "apply_patch(...)"
    if name == "read_many":
        paths = arguments.get("paths") or []
        return f"read_many({len(paths)} files)"
    if name == "git_commit":
        return f"git_commit({arguments.get('message', '')[:30]!r})"
    if name == "lsp":
        return f"lsp({arguments.get('action', '?')}, {arguments.get('path', '?')})"
    if name == "powershell":
        return f"powershell({_truncate(str(arguments.get('command', '')), 50)})"
    rendered = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    return f"{name}({_truncate(rendered, 50)})"


def _summarize_tool_output(tool_name: str, output: str) -> str:
    text = " ".join(str(output or "").split())
    if tool_name == "read_file" and "lines" in text.lower():
        return _truncate(text, 60)
    if tool_name in {"agent_worker", "agent_planner", "agent_explorer"}:
        return _truncate(text, 60)
    if "Tool error:" in text:
        return _truncate(text, 60)
    return _truncate(text, _MAX_DETAIL_CHARS)


def _truncate(text: str, limit: int = _MAX_DETAIL_CHARS) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
