"""Optional debug dumps for context compression (prose compaction)."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from langbridge_code.settings import CONTEXT_DEBUG_PERSIST
from langbridge_code.util.artifacts import agent_file_prefix, debug_trace_dir
from langbridge_code.util.trace_log import get_trace_context

_COUNTERS: dict[tuple[str, str, str], int] = {}
_COUNTER_LOCK = threading.Lock()


@dataclass(frozen=True)
class DebugAgentContext:
    run_log_path: object
    trace_id: str
    label: str
    instance_id: int | None = None


from langbridge_code.util.agent_debug import get_agent_debug


def current_debug_context() -> DebugAgentContext | None:
    trace = get_trace_context()
    if trace is None:
        return None
    label, instance_id = get_agent_debug()
    return DebugAgentContext(
        run_log_path=trace.run_log_path,
        trace_id=trace.trace_id,
        label=label,
        instance_id=instance_id,
    )


def enabled() -> bool:
    return CONTEXT_DEBUG_PERSIST


def _next_compress_seq(run_log_path, trace_id: str, prefix: str) -> int:
    key = (str(run_log_path), trace_id, prefix)
    with _COUNTER_LOCK:
        value = _COUNTERS.get(key, 0) + 1
        _COUNTERS[key] = value
    return value


def _write_text(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def format_raws(rounds: list[list[dict]]) -> str:
    lines: list[str] = []
    for index, round_messages in enumerate(rounds, start=1):
        lines.append(f"## Round {index}")
        lines.append("")
        for message in round_messages:
            role = message.get("role", message.get("type", "unknown"))
            if message.get("type") == "function_call":
                name = message.get("name", "tool")
                args = message.get("arguments", "{}")
                lines.append(f"- **tool** `{name}`({args})")
                continue
            if message.get("type") == "function_call_output":
                output = str(message.get("output", ""))
                lines.append(f"- **result** {_truncate(output, 2000)}")
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            lines.append(f"- **{role}** {_truncate(str(content), 2000)}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def record_prose_compression(
    *,
    ctx: DebugAgentContext | None = None,
    seq: int | None = None,
    input_text: str,
    output: str,
) -> None:
    if not enabled():
        return
    ctx = ctx or current_debug_context()
    if ctx is None:
        return
    directory = debug_trace_dir(ctx.run_log_path, ctx.trace_id)
    if directory is None:
        return
    prefix = agent_file_prefix(ctx.label, ctx.instance_id)
    counter = seq if seq is not None else _next_compress_seq(ctx.run_log_path, ctx.trace_id, prefix)
    base = f"{prefix}_prose_c{counter:03d}"
    meta = {
        "type": "prose",
        "agent": ctx.label,
        "instance_id": ctx.instance_id,
        "seq": counter,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    _write_text(directory / f"{base}_meta.json", json.dumps(meta, indent=2))
    _write_text(directory / f"{base}_input.md", input_text)
    _write_text(directory / f"{base}_output.md", output)


def record_state_snapshot(*, ctx: DebugAgentContext | None = None, content: str) -> None:
    if not enabled():
        return
    ctx = ctx or current_debug_context()
    if ctx is None:
        return
    directory = debug_trace_dir(ctx.run_log_path, ctx.trace_id)
    if directory is None:
        return
    _write_text(directory / "state_latest.md", content)


def _ctx_from_thread() -> DebugAgentContext | None:
    return current_debug_context()


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
