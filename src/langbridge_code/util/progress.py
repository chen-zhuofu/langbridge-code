"""Session progress.md — cross-turn memory for the main agent (includes goal state)."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

_progress_lock = threading.Lock()

from langbridge_code.settings import MAX_SESSION_SUMMARY_INPUT_CHARS, PROGRESS_MAX_FRACTION
from langbridge_code.util.artifacts import progress_path as artifact_progress_path

PROGRESS_HEADER = "# Session progress\n"
GOAL_HEADER = "## Goal\n"
_TURN_HEADER_RE = re.compile(r"^## Turns? (\d+)(?:-(\d+))?\s*$", re.MULTILINE)
_TURN_SECTION_SPLIT_RE = re.compile(r"^## Turns? \d+(?:-\d+)?\s*$", re.MULTILINE)


@dataclass
class GoalBlock:
    condition: str = ""
    status: str = ""
    turns: str = ""
    last_check: str = ""
    next_step: str = ""


def progress_path(run_log_path):
    path = artifact_progress_path(run_log_path)
    if path is not None:
        return path
    return run_log_path.with_name("progress.md") if run_log_path else None


def read_progress(run_log_path) -> str:
    if not run_log_path:
        return ""
    path = progress_path(run_log_path)
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_progress(run_log_path, content: str) -> None:
    path = progress_path(run_log_path)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def parse_goal_block(content: str) -> GoalBlock | None:
    if GOAL_HEADER not in content:
        return None
    start = content.index(GOAL_HEADER)
    rest = content[start + len(GOAL_HEADER) :]
    end = rest.find("\n## ")
    section = rest if end < 0 else rest[:end]
    block = GoalBlock()
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- **Condition:**"):
            block.condition = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- **Status:**"):
            block.status = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- **Turns:**"):
            block.turns = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- **Last check:**"):
            block.last_check = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- **Next:**"):
            block.next_step = stripped.split(":", 1)[1].strip()
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


def upsert_goal_block(run_log_path, goal) -> None:
    existing = read_progress(run_log_path).strip()
    goal_text = render_goal_block(goal)
    if not goal_text:
        return
    if not existing or existing == PROGRESS_HEADER.strip():
        write_progress(run_log_path, PROGRESS_HEADER + goal_text + "\n")
        return
    if GOAL_HEADER in existing:
        start = existing.index(GOAL_HEADER)
        rest = existing[start + len(GOAL_HEADER) :]
        end = rest.find("\n## Turn")
        if end < 0:
            end = rest.find("\n## Turns")
        if end >= 0:
            tail = rest[end + 1 :]
            body = existing[:start].rstrip() + "\n\n" + goal_text + "\n\n" + tail.lstrip()
        else:
            body = existing[:start].rstrip() + "\n\n" + goal_text
    else:
        header = existing if existing.startswith("#") else PROGRESS_HEADER + existing
        body = header.rstrip() + "\n\n" + goal_text
    write_progress(run_log_path, body)


def remove_goal_block(run_log_path) -> None:
    existing = read_progress(run_log_path).strip()
    if GOAL_HEADER not in existing:
        return
    start = existing.index(GOAL_HEADER)
    rest = existing[start + len(GOAL_HEADER) :]
    end = rest.find("\n## ")
    if end >= 0:
        body = existing[:start].rstrip() + "\n\n" + rest[end + 1 :].lstrip()
    else:
        body = existing[:start].rstrip()
    if body.strip() in ("", PROGRESS_HEADER.strip()):
        write_progress(run_log_path, PROGRESS_HEADER)
    else:
        write_progress(run_log_path, body)


def _build_continuation_directive(run_log_path, user_prompt: str) -> str:
    from langbridge_code.agents.common.todo_list import (
        clean_task_text,
        first_unfinished_task,
        is_continuation_request,
        load_tasks,
    )

    if not is_continuation_request(user_prompt):
        return ""
    tasks = load_tasks(run_log_path)
    next_task = first_unfinished_task(tasks)
    if next_task is None:
        return ""
    task_line = clean_task_text(next_task.description)
    return (
        "Continuation directive (binding for this turn):\n"
        "The user wants to resume the existing todo_list — not start a new project.\n"
        "- Call read_plan, then delegate agent_worker with exactly the next unchecked "
        "`- [ ]` subtask below.\n"
        "- Do NOT use ask_user or ask the user to choose between older chat topics "
        "(e.g. game code vs PPT) unless they explicitly named a new project this turn.\n"
        "- Do not treat a deliverable file already existing as proof the plan is finished; "
        "trust only `[x]` marks in the todo_list.\n"
        f"Next unchecked subtask: {task_line}"
    )


def last_progress_turn_id(run_log_path) -> int:
    """Highest turn id covered in progress.md (0 if none)."""
    content = read_progress(run_log_path)
    ids = []
    for match in _TURN_HEADER_RE.finditer(content):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        ids.append(end)
    return max(ids, default=0)


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
    del include_history_briefing  # progress is pinned as a <progress> block
    prompt = (user_prompt or "").strip()
    continuation = _build_continuation_directive(run_log_path, prompt)
    if continuation and prompt:
        return f"{continuation}\n\n---\n\nCurrent request:\n{prompt}"
    if continuation:
        return continuation
    return prompt


def build_main_agent_messages(run_log_path, user_prompt: str) -> list[dict]:
    from langbridge_code.agents.main_agent import langbridge_system_prompt

    return [
        {"role": "system", "content": langbridge_system_prompt()},
        {"role": "user", "content": build_turn_user_content(run_log_path, user_prompt)},
    ]


def _turn_progress_source(run_log_path, turn_id: int, *, user: str, assistant: str) -> str:
    from langbridge_code.util.trace_log import read_latest_trace_for_turn

    lines = [f"## Turn {turn_id}", "", f"**In:** {user.strip()}", ""]
    trace_lines = read_latest_trace_for_turn(run_log_path, turn_id)
    if trace_lines:
        lines.append("Trace (concise):")
        lines.extend(f"- {line}" for line in trace_lines[-40:])
        lines.append("")
    if assistant.strip():
        lines.append(f"**Out:** {assistant.strip()}")
    return "\n".join(lines)


def turn_progress_stub(turn_id: int, *, user: str = "", assistant: str = "") -> str:
    lines = [f"## Turn {turn_id}", "", f"**In:** {(user or '').strip()}", ""]
    if (assistant or "").strip():
        lines.append(f"**Out:** {assistant.strip()}")
    return "\n".join(lines)


NOTE_PREFIX = "- **Note:**"


def _turn_section_span(content: str, turn_id: int) -> tuple[int, int] | None:
    """(start, end) char span of the ``## Turn N`` section, or None."""
    pattern = re.compile(
        rf"^## Turn {turn_id}\s*$.*?(?=^## Turns? \d+(?:-\d+)?\s*$|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content or "")
    if not match:
        return None
    return match.start(), match.end()


def _extract_turn_notes(content: str, turn_id: int) -> list[str]:
    span = _turn_section_span(content or "", turn_id)
    if span is None:
        return []
    section = content[span[0] : span[1]]
    return [
        line.rstrip()
        for line in section.splitlines()
        if line.strip().startswith(NOTE_PREFIX)
    ]


def append_progress_note(run_log_path, turn_id: int, text: str) -> str:
    """Append a mid-turn note to progress.md under the current turn section.

    The main agent may record progress whenever it finishes something — it
    does not have to wait for the turn to end. Notes survive the end-of-turn
    stub/enrich rewrite of the same turn section.
    """
    note = " ".join((text or "").split()).strip()
    if not note:
        return "Note was empty; nothing recorded."
    if not run_log_path:
        return "No session directory; note not recorded."
    line = f"{NOTE_PREFIX} {note}"
    heading = f"## Turn {int(turn_id or 0)}"
    with _progress_lock:
        existing = read_progress(run_log_path).strip()
        span = _turn_section_span(existing, int(turn_id or 0))
        if span is None:
            base = existing if existing else PROGRESS_HEADER.strip()
            body = base.rstrip() + "\n\n" + heading + "\n\n" + line + "\n"
        else:
            section = existing[span[0] : span[1]].rstrip()
            body = existing[: span[0]] + section + "\n" + line + "\n" + existing[span[1] :]
        write_progress(run_log_path, body)
    return f"Noted in progress.md: {note}"


def _remove_turn_section(content: str, turn_id: int) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    pattern = re.compile(
        rf"^## Turn {turn_id}\s*$.*?(?=^## Turns? \d+(?:-\d+)?\s*$|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    cleaned = pattern.sub("", text).strip()
    if cleaned in ("", PROGRESS_HEADER.strip()):
        return PROGRESS_HEADER.strip()
    return cleaned


def _append_progress_body(run_log_path, existing: str, section: str) -> str:
    section = section.strip()
    if not existing:
        return PROGRESS_HEADER + section + "\n"
    return existing.rstrip() + "\n\n" + section + "\n"


@dataclass
class _ProgressTurnSection:
    start: int
    end: int
    body: str

    @property
    def heading(self) -> str:
        if self.start == self.end:
            return f"## Turn {self.start}"
        return f"## Turns {self.start}-{self.end}"


def _split_progress_document(content: str) -> tuple[str, str, list[_ProgressTurnSection]]:
    """Return (preamble_with_goal, leftover_non_turn, turn_sections)."""
    text = (content or "").strip()
    if not text:
        return PROGRESS_HEADER.strip(), "", []

    matches = list(_TURN_SECTION_SPLIT_RE.finditer(text))
    if not matches:
        return text, "", []

    preamble = text[: matches[0].start()].rstrip()
    if not preamble:
        preamble = PROGRESS_HEADER.strip()

    sections: list[_ProgressTurnSection] = []
    for index, match in enumerate(matches):
        header = match.group(0).strip()
        parsed = _TURN_HEADER_RE.match(header)
        if not parsed:
            continue
        start = int(parsed.group(1))
        end = int(parsed.group(2)) if parsed.group(2) else start
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        sections.append(_ProgressTurnSection(start=start, end=end, body=body))
    return preamble, "", sections


def _render_progress_document(preamble: str, sections: list[_ProgressTurnSection]) -> str:
    parts = [preamble.rstrip() or PROGRESS_HEADER.strip()]
    for section in sections:
        parts.append(section.heading + ("\n\n" + section.body if section.body else ""))
    return "\n\n".join(parts) + "\n"


def _merge_progress_sections_llm(api_key, model, sections: list[_ProgressTurnSection]) -> str:
    from langbridge_code.llm.client import create_model_response
    from langbridge_code.llm.parse import extract_output_text, truncate_text

    if not sections:
        return ""
    start = sections[0].start
    end = sections[-1].end
    heading = f"## Turns {start}-{end}" if start != end else f"## Turn {start}"
    source = "\n\n".join(f"{sec.heading}\n{sec.body}" for sec in sections)
    prompt = (
        "Merge these session progress turn sections into ONE concise section.\n"
        f"Use this exact heading as the first line: {heading}\n"
        "Keep factual bullets about work done, files, tests, and open items.\n"
        "Drop redundancy. No preamble.\n\n"
        f"{truncate_text(source, MAX_SESSION_SUMMARY_INPUT_CHARS)}"
    )
    response = create_model_response(
        api_key,
        model,
        [
            {"role": "system", "content": "You merge session progress notes."},
            {"role": "user", "content": prompt},
        ],
        label="progress_merge",
    )
    text = extract_output_text(response.get("output", [])).strip()
    if not text:
        bullets = []
        for sec in sections:
            for line in sec.body.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    bullets.append(stripped)
        body = "\n".join(bullets) if bullets else "- (merged turns)"
        return f"{heading}\n{body}"
    if not text.lstrip().startswith("##"):
        text = f"{heading}\n{text}"
    return text


def maybe_compact_progress(api_key, model, run_log_path) -> bool:
    """Merge middle progress turns when the file exceeds PROGRESS_MAX_FRACTION of the window."""
    from langbridge_code.context.common.budget import estimate_tokens
    from langbridge_code.llm.model_context import model_context_window

    if not run_log_path or not api_key or not model:
        return False
    budget = max(1, int(model_context_window(model) * PROGRESS_MAX_FRACTION))
    changed = False
    while True:
        with _progress_lock:
            content = read_progress(run_log_path).strip()
        if estimate_tokens(content) <= budget:
            break
        preamble, _, sections = _split_progress_document(content)
        if len(sections) < 3:
            break
        middle = sections[1:-1]
        merged_text = _merge_progress_sections_llm(api_key, model, middle)
        merged_sections = _split_progress_document(merged_text)[2]
        if not merged_sections:
            start = middle[0].start
            end = middle[-1].end
            body = "\n".join(
                line
                for sec in middle
                for line in sec.body.splitlines()
                if line.strip().startswith("- ")
            ) or "- (merged turns)"
            merged_sections = [_ProgressTurnSection(start=start, end=end, body=body)]
        new_sections = [sections[0], merged_sections[0], sections[-1]]
        body = _render_progress_document(preamble, new_sections)
        with _progress_lock:
            write_progress(run_log_path, body)
        changed = True
        if estimate_tokens(body) <= budget:
            break
        # Avoid infinite loops if merge did not shrink enough.
        if len(new_sections) >= len(sections):
            break
    return changed


def append_turn_progress_stub(
    run_log_path,
    turn_id: int,
    *,
    user: str = "",
    assistant: str = "",
) -> None:
    with _progress_lock:
        existing = read_progress(run_log_path).strip()
        notes = _extract_turn_notes(existing, turn_id)
        existing = _remove_turn_section(existing, turn_id)
        section = turn_progress_stub(turn_id, user=user, assistant=assistant)
        if notes:
            section = section + "\n" + "\n".join(notes)
        body = _append_progress_body(run_log_path, existing, section)
        write_progress(run_log_path, body)


def schedule_append_turn_progress(
    api_key,
    model,
    run_log_path,
    turn_id: int,
    *,
    user: str = "",
    assistant: str = "",
) -> None:
    """Write a stub immediately; enrich with an LLM summary in the background."""

    def worker() -> None:
        try:
            append_turn_progress(
                api_key,
                model,
                run_log_path,
                turn_id,
                user=user,
                assistant=assistant,
                replace_turn=True,
            )
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def finalize_main_agent_turn(
    api_key,
    model,
    run_log_path,
    turn_id: int,
    *,
    user: str,
    assistant: str,
) -> None:
    """Persist progress when a main-agent turn ends (any outcome)."""
    from langbridge_code.util.session_traces import append_progress_boundary

    outcome = (assistant or "").strip() or "(turn ended without a reply)"
    append_turn_progress_stub(run_log_path, turn_id, user=user, assistant=outcome)
    append_progress_boundary(run_log_path, turn_id)
    maybe_compact_progress(api_key, model, run_log_path)
    schedule_append_turn_progress(
        api_key,
        model,
        run_log_path,
        turn_id,
        user=user,
        assistant=outcome,
    )


def _summarize_turn_progress(api_key, model, source: str) -> str:
    if not source.strip():
        return "- (no activity recorded)"
    from langbridge_code.llm.client import create_model_response
    from langbridge_code.llm.parse import extract_output_text, truncate_text

    prompt = (
        "Write concise progress bullets for the main coding agent's session log.\n"
        "Audience: the same agent on the next user turn. Be factual and specific.\n"
        "Keep **In:** and **Out:** lines exactly as provided. Only add bullet lines "
        "between them summarizing delegated work, files/tests, outcomes, open items.\n"
        "Format under the ## Turn N heading. No preamble.\n\n"
        f"{truncate_text(source, MAX_SESSION_SUMMARY_INPUT_CHARS)}"
    )
    response = create_model_response(
        api_key,
        model,
        [
            {"role": "system", "content": "You write terse session progress notes."},
            {"role": "user", "content": prompt},
        ],
        label="progress",
    )
    text = extract_output_text(response.get("output", [])).strip()
    return text or "- Turn completed (summary unavailable)."


def append_turn_progress(
    api_key,
    model,
    run_log_path,
    turn_id: int,
    *,
    user: str = "",
    assistant: str = "",
    replace_turn: bool = False,
) -> str:
    user_text = user or ""
    assistant_text = assistant or ""
    source = _turn_progress_source(run_log_path, turn_id, user=user_text, assistant=assistant_text)
    section = _summarize_turn_progress(api_key, model, source)
    with _progress_lock:
        existing = read_progress(run_log_path).strip()
        if replace_turn:
            notes = _extract_turn_notes(existing, turn_id)
            existing = _remove_turn_section(existing, turn_id)
            missing = [note for note in notes if note.strip() not in section]
            if missing:
                section = section.rstrip() + "\n" + "\n".join(missing)
        body = _append_progress_body(run_log_path, existing, section)
        write_progress(run_log_path, body)
    maybe_compact_progress(api_key, model, run_log_path)
    return read_progress(run_log_path)
