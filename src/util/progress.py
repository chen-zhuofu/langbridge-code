"""Session / per-task progress.md (Claude Code Session Memory style).

``note_progress`` forks an Edit-restricted writer that updates section bodies
in place. The in-memory ``<progress>`` block is loaded only on resume and after
context compaction — mid-turn writes update the file only.
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
# Cap for the pinned <progress> block. Larger files stay on disk; the model
# is pointed at the path so it can read_file the rest.
PROGRESS_CONTEXT_MAX_TOKENS = 20_000
# Note body starts at #### sections (or legacy ## Turn after a goal block).
_NOTE_BODY_START_RE = re.compile(r"^(#### |## Turn\b)", re.MULTILINE)


@dataclass
class GoalBlock:
    condition: str = ""
    status: str = ""
    turns: str = ""
    last_check: str = ""
    next_step: str = ""


def progress_path(
    run_log_path,
    task_name: str | None = None,
    role: str | None = None,
):
    """Session progress.md, or the per-task/per-role file when task_name is given."""
    if task_name:
        return artifact_task_progress_path(
            run_log_path, task_name, role=role or "Worker"
        )
    return artifact_progress_path(run_log_path)


def read_progress(
    run_log_path,
    task_name: str | None = None,
    role: str | None = None,
) -> str:
    if not run_log_path:
        return ""
    path = progress_path(run_log_path, task_name, role=role)
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def clip_progress_for_context(
    content: str,
    *,
    run_log_path,
    task_name: str | None = None,
    role: str | None = None,
    max_tokens: int = PROGRESS_CONTEXT_MAX_TOKENS,
) -> str:
    """Truncate progress text before pinning it into ``<progress>``.

    Disk file is untouched. When over budget, keep the head and tell the model
    where to read the full note.
    """
    from langbridge_code.context.common.budget import estimate_tokens

    text = (content or "").strip()
    if not text:
        return ""
    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return text
    path = progress_path(run_log_path, task_name, role=role)
    path_label = str(path.resolve()) if path is not None else "progress.md"
    notice = (
        f"\n\n[progress truncated for context — kept the first ~{max_tokens} tokens; "
        f"full file: {path_label}]"
    )
    notice_tokens = estimate_tokens(notice)
    budget = max(64, max_tokens - notice_tokens)
    # Same rough char budget Claude uses (tokens ≈ chars/4), then shrink if needed.
    approx_chars = max(256, budget * 4)
    clipped = text[:approx_chars].rstrip()
    while clipped and estimate_tokens(clipped) > budget:
        clipped = clipped[: max(0, len(clipped) - 512)].rstrip()
    if not clipped:
        return notice.strip()
    # Prefer cutting on a paragraph boundary when we still have room nearby.
    last_break = clipped.rfind("\n\n")
    if last_break >= max(256, len(clipped) // 2):
        clipped = clipped[:last_break].rstrip()
    return clipped + notice


def write_progress(
    run_log_path,
    content: str,
    task_name: str | None = None,
    role: str | None = None,
) -> None:
    path = progress_path(run_log_path, task_name, role=role)
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


def ensure_progress_template(
    run_log_path,
    task_name: str | None = None,
    role: str | None = None,
) -> str:
    """Ensure progress.md exists with the section template; return current text.

    Empty / header-only files get the Session Memory-style template. Existing
    note bodies (including legacy notes without italic descriptions) are kept.
    A ``## Goal`` block is preserved when seeding.
    """
    from langbridge_code.prompt.fork.progress_note import (
        SESSION_PROGRESS_TEMPLATE,
        TASK_PROGRESS_TEMPLATE,
    )

    if not run_log_path:
        return ""
    template = TASK_PROGRESS_TEMPLATE if task_name else SESSION_PROGRESS_TEMPLATE
    with _progress_lock:
        existing = read_progress(run_log_path, task_name, role=role).strip()
        note = _extract_note_body(existing)
        if note:
            return existing + ("\n" if not existing.endswith("\n") else "")
        goal = _extract_goal_markdown(existing)
        # Drop the template's own "# Session progress" header; we rejoin below.
        body = template.strip()
        if body.startswith(PROGRESS_HEADER.strip()):
            body = body[len(PROGRESS_HEADER.strip()) :].lstrip()
        parts = [PROGRESS_HEADER.strip()]
        if goal:
            parts.append(goal)
        parts.append(body)
        content = "\n\n".join(parts) + "\n"
        write_progress(run_log_path, content, task_name, role=role)
        return content


def note_progress_edit_succeeded(
    before: str,
    after: str,
    *,
    run_log_path=None,
    turn_id: int | None = None,
    task_name: str | None = None,
) -> str:
    """Return the tool result string after an Edit-based progress fork."""
    before_text = (before or "").strip()
    after_text = (after or "").strip()
    if not after_text or after_text == before_text:
        return "Progress note fork made no file changes; nothing recorded."
    if task_name is None and run_log_path is not None and turn_id is not None:
        from langbridge_code.util.session_traces import append_progress_boundary

        append_progress_boundary(run_log_path, turn_id)
    note = _extract_note_body(after_text) or after_text
    summary = " ".join(note.split())
    if len(summary) > 200:
        summary = summary[:197] + "..."
    return f"Noted in progress.md: {summary}"


def write_progress_note(
    run_log_path,
    text: str,
    task_name: str | None = None,
    *,
    turn_id: int | None = None,
    role: str | None = None,
) -> str:
    """Full-body override (tests / legacy). Prefer the Edit-based fork in agents."""
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
        existing = read_progress(run_log_path, task_name, role=role).strip()
        goal = _extract_goal_markdown(existing)
        parts = [PROGRESS_HEADER.strip()]
        if goal:
            parts.append(goal)
        parts.append(note)
        write_progress(run_log_path, "\n\n".join(parts) + "\n", task_name, role=role)
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
