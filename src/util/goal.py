"""Session-scoped autonomous goals stored in progress.md."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langbridge_code.settings import GOAL_DEFAULT_MAX_TURNS
from langbridge_code.util.progress import parse_goal_block, read_progress, remove_goal_block, upsert_goal_block

STATUS_ACTIVE = "active"
STATUS_ACHIEVED = "achieved"
STATUS_PAUSED = "paused"
STATUS_CLEARED = "cleared"

_TURN_LIMIT_RE = re.compile(
    r"\s+or\s+stop\s+after\s+(\d+)\s+turns?\s*$",
    re.IGNORECASE,
)


@dataclass
class SessionGoal:
    condition: str
    status: str = STATUS_ACTIVE
    turn_count: int = 0
    max_turns: int | None = None
    last_reason: str = ""
    last_guidance: str = ""
    created_at: str = field(default_factory=lambda: _now_iso())

    @property
    def active(self) -> bool:
        return self.status == STATUS_ACTIVE


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_goal_command(text: str) -> tuple[str, int | None]:
    stripped = (text or "").strip()
    if not stripped:
        return "", None
    match = _TURN_LIMIT_RE.search(stripped)
    if not match:
        return stripped, None
    condition = stripped[: match.start()].strip()
    max_turns = int(match.group(1))
    return condition or stripped, max_turns


def load_goal(run_log_path) -> SessionGoal | None:
    if not run_log_path:
        return None
    block = parse_goal_block(read_progress(run_log_path))
    if block is None or not block.condition:
        return None
    turn_count = 0
    max_turns = None
    if block.turns:
        parts = block.turns.split("/")
        try:
            turn_count = int(parts[0].strip())
        except ValueError:
            turn_count = 0
        if len(parts) > 1:
            try:
                max_turns = int(parts[1].strip())
            except ValueError:
                max_turns = None
    return SessionGoal(
        condition=block.condition,
        status=block.status or STATUS_ACTIVE,
        turn_count=turn_count,
        max_turns=max_turns,
        last_reason=block.last_check,
        last_guidance=block.next_step,
    )


def save_goal(run_log_path, goal: SessionGoal | None) -> None:
    if not run_log_path:
        return
    if goal is None:
        remove_goal_block(run_log_path)
        return
    upsert_goal_block(run_log_path, goal)


def clear_goal(run_log_path) -> None:
    if run_log_path:
        remove_goal_block(run_log_path)


def new_goal(condition: str, *, max_turns: int | None = None) -> SessionGoal:
    parsed, parsed_limit = parse_goal_command(condition)
    effective = parsed or condition.strip()
    limit = max_turns if max_turns is not None else parsed_limit
    if limit is None:
        limit = GOAL_DEFAULT_MAX_TURNS
    return SessionGoal(condition=effective, max_turns=limit)


def build_continuation_prompt(goal: SessionGoal) -> str:
    reason = (goal.last_reason or "Continue working toward the goal.").strip()
    guidance = (goal.last_guidance or "").strip()
    lines = [
        "The goal evaluator says the completion condition is NOT met yet.",
        f"Evaluator: {reason}",
    ]
    if guidance:
        lines.append(f"Do next: {guidance}")
    lines.append("")
    lines.append(f"Keep working toward this completion condition:\n{goal.condition}")
    lines.append("")
    lines.append(
        "Do not reply with a final summary yet — use tools and subagents until the "
        "condition is demonstrably satisfied."
    )
    return "\n".join(lines)


def format_goal_status(goal: SessionGoal) -> str:
    lines = [
        f"Goal ({goal.status})",
        f"Condition: {goal.condition}",
        f"Turns: {goal.turn_count}"
        + (f" / {goal.max_turns}" if goal.max_turns is not None else ""),
    ]
    if goal.last_reason:
        lines.append(f"Last check: {goal.last_reason}")
    if goal.last_guidance:
        lines.append(f"Next: {goal.last_guidance}")
    return "\n".join(lines)


def goal_turn_limit_reached(goal: SessionGoal) -> bool:
    if goal.max_turns is None:
        return False
    return goal.turn_count >= goal.max_turns
