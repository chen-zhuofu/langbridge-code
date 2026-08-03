"""Per-subagent raw markdown traces and compaction audit records.

Each subagent dispatch writes
{session}/tasks/{task-slug}/traces/{role}-{n}.md — the same "## Round N" +
```json block format as the main agent's traces.md. These paths are
engine/human only (not agent-readable via read_file).
"""
from __future__ import annotations

import itertools
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from langbridge_code.context.common.budget import estimate_tokens
from langbridge_code.llm.model_context import model_context_window
from langbridge_code.settings import TRACES_RESUME_MAX_FRACTION
from langbridge_code.util.artifacts import (
    artifact_dir,
    attachments_dir,
    slug_first_message,
    task_traces_dir,
)

_INLINE_COMPACTION_CHARS = 4_000
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_COMPACTION_SEQUENCE = itertools.count()

_JSON_BLOCK_RE = re.compile(r"^```json\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _role_slug(role: str) -> str:
    return slug_first_message(role or "agent").lower()


def reserve_agent_trace(run_log_path, role: str, task_name: str) -> tuple[Path | None, int | None]:
    """Reserve ``tasks/{slug}/traces/{role}-{id}.md``; ids start at zero per task."""
    directory = task_traces_dir(run_log_path, task_name)
    if directory is None:
        return None, None
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"{_role_slug(role)}-"
    lock = _lock_for(directory / f".{prefix}counter")
    with lock:
        instance_id = 0
        while (directory / f"{prefix}{instance_id}.md").exists():
            instance_id += 1
        path = directory / f"{prefix}{instance_id}.md"
        header = (
            f"# {role} trace — task: {task_name} (instance {instance_id})\n"
            f"\nStarted: {_timestamp()}\n"
        )
        path.write_text(header, encoding="utf-8")
    return path, instance_id


def append_agent_raw_round(
    path: Path | None,
    *,
    round_index: int,
    messages: list[dict],
) -> None:
    """Append one uncompressed, non-system message round to an agent trace."""
    if path is None or not messages:
        return
    filtered = [
        item for item in messages if isinstance(item, dict) and item.get("role") != "system"
    ]
    if not filtered:
        return
    payload = json.dumps(filtered, ensure_ascii=False, indent=2, default=str)
    block = f"\n## Round {round_index}\n\n```json\n{payload}\n```\n"
    with _lock_for(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)


def _trace_instance_id(path: Path) -> int:
    try:
        return int(path.stem.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return -1


def agent_trace_paths(
    run_log_path,
    role: str,
    task_name: str,
    *,
    exclude: Path | None = None,
) -> list[Path]:
    """Existing traces for one role/task, oldest dispatch first."""
    directory = task_traces_dir(run_log_path, task_name)
    if directory is None or not directory.exists():
        return []
    prefix = f"{_role_slug(role)}-"
    excluded = Path(exclude).resolve() if exclude is not None else None
    paths = []
    for path in directory.glob(f"{prefix}*.md"):
        if excluded is not None and path.resolve() == excluded:
            continue
        paths.append(path)
    return sorted(paths, key=_trace_instance_id)


def _read_agent_rounds(paths: list[Path]) -> list[list[dict]]:
    rounds: list[list[dict]] = []
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _JSON_BLOCK_RE.finditer(content):
            try:
                messages = json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(messages, list) and messages:
                rounds.append(messages)
    return rounds


def _render_resume_rounds(rounds: list[list[dict]], role: str) -> str:
    payload = json.dumps(rounds, ensure_ascii=False, indent=2, default=str)
    return f"## Raw {role} traces from earlier dispatches\n\n```json\n{payload}\n```"


def build_agent_resume_background(
    run_log_path,
    *,
    role: str,
    task_name: str,
    model: str,
    progress: str = "",
    exclude_trace: Path | None = None,
) -> str:
    """Resume one subagent task from progress plus its prior raw trace tail.

    This mirrors the main-agent cold-start policy: use raw traces directly when
    they fit; otherwise use the durable progress note plus the newest complete
    raw rounds that fit the remaining resume budget.
    """
    progress = (progress or "").strip()
    rounds = _read_agent_rounds(
        agent_trace_paths(
            run_log_path,
            role,
            task_name,
            exclude=exclude_trace,
        )
    )
    if not rounds:
        return progress

    budget = max(1, int(model_context_window(model) * TRACES_RESUME_MAX_FRACTION))
    full = _render_resume_rounds(rounds, role)
    if estimate_tokens(full) <= budget:
        return full

    remaining = max(0, budget - estimate_tokens(progress))
    kept: list[list[dict]] = []
    for round_messages in reversed(rounds):
        candidate = [round_messages, *kept]
        if estimate_tokens(_render_resume_rounds(candidate, role)) <= remaining:
            kept = candidate
            continue
        break
    if not kept:
        return progress
    tail = _render_resume_rounds(kept, role)
    return f"{progress}\n\n{tail}".strip()


def append_compaction_event(run_log_path, event: dict) -> Path | None:
    """Append a compaction index record; oversized full events become attachments."""
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "compactions.jsonl"
    payload = dict(event)
    payload.setdefault("timestamp", _timestamp())
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(rendered) > _INLINE_COMPACTION_CHARS:
        attachments = attachments_dir(run_log_path)
        attachments.mkdir(parents=True, exist_ok=True)
        sequence = next(_COMPACTION_SEQUENCE)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S.%fZ")
        attachment = attachments / f"compaction-{stamp}-{sequence:03d}.json"
        attachment.write_text(rendered + "\n", encoding="utf-8")
        record = {
            "type": payload.get("type", "compaction"),
            "timestamp": payload["timestamp"],
            "role": payload.get("role"),
            "task_name": payload.get("task_name"),
            "instance_id": payload.get("instance_id"),
            "before": payload.get("before"),
            "after": payload.get("after"),
            "full_event_attachment": str(attachment.relative_to(directory)),
        }
    else:
        record = payload
    with _lock_for(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
