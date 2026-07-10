"""Session progress.md — cross-turn memory for the main agent (includes goal state)."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

_progress_lock = threading.Lock()

from langbridge_code.settings import MAX_SESSION_SUMMARY_INPUT_CHARS
from langbridge_code.util.artifacts import progress_path as artifact_progress_path

PROGRESS_HEADER = "# Session progress\n"
GOAL_HEADER = "## Goal\n"
_TURN_HEADER_RE = re.compile(r"^## Turn (\d+)\s*$", re.MULTILINE)


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


def build_turn_user_content(run_log_path, user_prompt: str) -> str:
    from langbridge_code.util.session import recent_session_dialogue

    progress = read_progress(run_log_path).strip()
    prompt = (user_prompt or "").strip()
    parts = []
    if progress and progress != PROGRESS_HEADER.strip():
        parts.append(
            "Session progress from prior turns (authoritative — do not rely on older chat):\n\n"
            f"{progress}"
        )
    dialogue = recent_session_dialogue(run_log_path, limit=3).strip()
    if dialogue:
        parts.append(f"Recent session dialogue:\n\n{dialogue}")
    continuation = _build_continuation_directive(run_log_path, prompt)
    if continuation:
        parts.append(continuation)
    if prompt:
        if parts:
            parts.append(f"Current request:\n{prompt}")
        else:
            return prompt
    if not parts:
        return ""
    return "\n\n---\n\n".join(parts)


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


def _remove_turn_section(content: str, turn_id: int) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    pattern = re.compile(
        rf"^## Turn {turn_id}\s*$.*?(?=^## Turn \d+\s*$|\Z)",
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


def append_turn_progress_stub(
    run_log_path,
    turn_id: int,
    *,
    user: str = "",
    assistant: str = "",
) -> None:
    with _progress_lock:
        existing = read_progress(run_log_path).strip()
        existing = _remove_turn_section(existing, turn_id)
        body = _append_progress_body(
            run_log_path,
            existing,
            turn_progress_stub(turn_id, user=user, assistant=assistant),
        )
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
    """Persist session + progress when a main-agent turn ends (any outcome)."""
    from langbridge_code.util.logging import write_turn_complete

    outcome = (assistant or "").strip() or "(turn ended without a reply)"
    write_turn_complete(run_log_path, turn_id, user, outcome)
    append_turn_progress_stub(run_log_path, turn_id, user=user, assistant=outcome)
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
    from langbridge_code.util.logging import read_turn_record

    record = read_turn_record(run_log_path, turn_id)
    user_text = user or (record.get("user") if record else "") or ""
    assistant_text = assistant or (record.get("assistant") if record else "") or ""
    source = _turn_progress_source(run_log_path, turn_id, user=user_text, assistant=assistant_text)
    section = _summarize_turn_progress(api_key, model, source)
    with _progress_lock:
        existing = read_progress(run_log_path).strip()
        if replace_turn:
            existing = _remove_turn_section(existing, turn_id)
        body = _append_progress_body(run_log_path, existing, section)
        write_progress(run_log_path, body)
    return body
