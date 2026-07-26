"""Session / per-task progress.md — single overridable note (Claude Code style).

Each ``note_progress`` fork rewrites the whole note from the live context.
The in-memory ``<progress>`` block is loaded only on resume and after context
compaction — mid-turn writes update the file only (raw messages already hold
the full transcript).
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

_progress_lock = threading.Lock()

from langbridge_code.util.artifacts import progress_path as artifact_progress_path
from langbridge_code.util.artifacts import task_progress_path as artifact_task_progress_path

PROGRESS_HEADER = "# Session progress\n"
GOAL_HEADER = "## Goal\n"
# Note body starts at #### sections (or legacy ## Turn after a goal block).
_NOTE_BODY_START_RE = re.compile(r"^(#### |## Turn\b)", re.MULTILINE)


@dataclass
class GoalBlock:
    condition: str = ""
    status: str = ""
    turns: str = ""
    last_check: str = ""
    next_step: str = ""


def progress_path(run_log_path, task_name: str | None = None):
    """Session progress.md, or the per-task file when task_name is given."""
    if task_name:
        return artifact_task_progress_path(run_log_path, task_name)
    return artifact_progress_path(run_log_path)


def read_progress(run_log_path, task_name: str | None = None) -> str:
    if not run_log_path:
        return ""
    path = progress_path(run_log_path, task_name)
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_progress(run_log_path, content: str, task_name: str | None = None) -> None:
    path = progress_path(run_log_path, task_name)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def parse_goal_block(content: str) -> GoalBlock | None:
    if GOAL_HEADER not in content:
        return None
    start = content.index(GOAL_HEADER)
    rest = content[start + len(GOAL_HEADER) :]
    match = _NOTE_BODY_START_RE.search(rest)
    section = rest if match is None else rest[: match.start()]
    block = GoalBlock()
    for line in section.splitlines():
        stripped = line.strip()
        for key, attr in (
            ("- **Condition:**", "condition"),
            ("- **Status:**", "status"),
            ("- **Turns:**", "turns"),
            ("- **Last check:**", "last_check"),
            ("- **Next:**", "next_step"),
        ):
            if stripped.startswith(key):
                setattr(block, attr, stripped[len(key) :].strip())
                break
    if not any((block.condition, block.status, block.turns, block.last_check, block.next_step)):
        return None
    return block


def render_goal_block(goal) -> str:
    from langbridge_code.util.goal import SessionGoal

    if not isinstance(goal, SessionGoal):
        return ""
    turns = f"{goal.turn_count}"
    if goal.max_turns is not None:
        turns += f" / {goal.max_turns}"
    lines = [
        GOAL_HEADER.rstrip(),
        f"- **Condition:** {goal.condition}",
        f"- **Status:** {goal.status}",
        f"- **Turns:** {turns}",
    ]
    if goal.last_reason:
        lines.append(f"- **Last check:** {goal.last_reason}")
    if goal.last_guidance:
        lines.append(f"- **Next:** {goal.last_guidance}")
    return "\n".join(lines)


def _strip_progress_header(content: str) -> str:
    text = (content or "").strip()
    header = PROGRESS_HEADER.strip()
    if text.startswith(header):
        return text[len(header) :].lstrip()
    return text


def _extract_goal_markdown(content: str) -> str:
    body = _strip_progress_header(content)
    if not body.startswith("## Goal"):
        return ""
    rest = body[len("## Goal") :].lstrip("\n")
    match = _NOTE_BODY_START_RE.search(rest)
    section = rest if match is None else rest[: match.start()]
    return (GOAL_HEADER + section).rstrip()


def _extract_note_body(content: str) -> str:
    """Everything after the header/goal — the overridable progress note."""
    body = _strip_progress_header(content)
    if not body:
        return ""
    if body.startswith("## Goal"):
        rest = body[len("## Goal") :].lstrip("\n")
        match = _NOTE_BODY_START_RE.search(rest)
        if match is None:
            # Goal-only file (no note yet).
            return ""
        return rest[match.start() :].strip()
    return body.strip()


def upsert_goal_block(run_log_path, goal) -> None:
    goal_text = render_goal_block(goal)
    if not goal_text:
        return
    with _progress_lock:
        existing = read_progress(run_log_path).strip()
        note = _extract_note_body(existing)
        parts = [PROGRESS_HEADER.strip(), goal_text]
        if note:
            parts.append(note)
        write_progress(run_log_path, "\n\n".join(parts) + "\n")


def remove_goal_block(run_log_path) -> None:
    with _progress_lock:
        existing = read_progress(run_log_path).strip()
        note = _extract_note_body(existing)
        if not note:
            write_progress(run_log_path, PROGRESS_HEADER)
            return
        write_progress(run_log_path, PROGRESS_HEADER + note + "\n")


def write_progress_note(
    run_log_path,
    text: str,
    task_name: str | None = None,
    *,
    turn_id: int | None = None,
) -> str:
    """Override the progress note body (preserve ## Goal). File only — no context inject."""
    note = (text or "").strip()
    if not note:
        return "Note was empty; nothing recorded."
    if not run_log_path:
        return "No session directory; note not recorded."
    # The goal/note boundary is detected by heading; a note that starts with
    # plain text would be swallowed into the goal section on the next goal
    # upsert. Anchor it under a recognized heading.
    if not _NOTE_BODY_START_RE.match(note):
        note = "#### Note\n" + note
    with _progress_lock:
        existing = read_progress(run_log_path, task_name).strip()
        goal = _extract_goal_markdown(existing)
        parts = [PROGRESS_HEADER.strip()]
        if goal:
            parts.append(goal)
        parts.append(note)
        write_progress(run_log_path, "\n\n".join(parts) + "\n", task_name)
    if task_name is None and turn_id is not None:
        from langbridge_code.util.session_traces import append_progress_boundary

        append_progress_boundary(run_log_path, turn_id)
    summary = " ".join(note.split())
    if len(summary) > 200:
        summary = summary[:197] + "..."
    return f"Noted in progress.md: {summary}"


def build_turn_user_content(
    run_log_path,
    user_prompt: str,
    *,
    include_history_briefing: bool = False,
) -> str:
    """Build the appendable user message for one main-agent turn.

    Progress is carried by the pinned ``<progress>`` context block (set by
    MainAgentSession) — not embedded here. ``include_history_briefing`` is
    retained for call-site compatibility but no longer inlines progress.md.
    """
    del run_log_path, include_history_briefing
    return (user_prompt or "").strip()


def build_main_agent_messages(run_log_path, user_prompt: str) -> list[dict]:
    from langbridge_code.agents.main_agent import langbridge_system_prompt

    return [
        {"role": "system", "content": langbridge_system_prompt()},
        {"role": "user", "content": build_turn_user_content(run_log_path, user_prompt)},
    ]
