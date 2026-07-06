"""Coder↔Reviewer loop without shared agent context; optimizer trace only."""
import subprocess

from langbridge_code.agents import workspace_git
from langbridge_code.agents.limits import now, over_time_budget
from langbridge_code.agents.multi_agent import (
    coder_ready_for_review,
    new_coder_session,
    new_reviewer_session,
    reviewer_review_passed,
    run_coder,
    run_reviewer,
)
from langbridge_code.settings import MAX_CODER_REVIEWER_ROUNDS, MAX_CODER_REVIEWER_SECONDS, WORKSPACE_ROOT
from langbridge_code.workflow import optimizer_trace
from langbridge_code.workflow.phases import emit_phase


def git_diff_since(snapshot: str | None) -> str:
    if not snapshot:
        result = subprocess.run(
            ["git", "diff", "--no-color"],
            cwd=WORKSPACE_ROOT,
            capture_output=True,
            text=True,
        )
        return result.stdout or ""
    result = subprocess.run(
        ["git", "diff", "--no-color", snapshot],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout or ""


def run_coder_reviewer_loop(
    api_key,
    model,
    task,
    context="",
    trace_sink=None,
    run_log_path=None,
    turn_id=None,
    approval_callback=None,
    phase_sink=None,
) -> tuple[bool, str]:
    """Return (passed, summary). On pass, changes are committed."""
    snapshot = workspace_git.snapshot_head()
    coder = new_coder_session(
        api_key,
        model,
        trace_sink=trace_sink,
        approval_callback=approval_callback,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )
    reviewer = new_reviewer_session(
        api_key,
        model,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )
    action = "coder"
    feedback = ""
    coder_report = ""
    reviewer_report = ""
    start = now()

    for round_index in range(MAX_CODER_REVIEWER_ROUNDS):
        if over_time_budget(start, MAX_CODER_REVIEWER_SECONDS):
            workspace_git.revert_snapshot(snapshot)
            optimizer_trace.append_event(
                run_log_path,
                {"event": "timeout", "round": round_index, "action": action},
            )
            return False, "Coder/reviewer loop timed out."

        if action == "coder":
            coder_report = run_coder(
                api_key,
                model,
                task,
                context,
                feedback,
                session=coder,
            )
            optimizer_trace.append_event(
                run_log_path,
                {
                    "event": "coder_turn",
                    "round": round_index,
                    "report": coder_report,
                    "feedback_in": feedback,
                },
            )
            if not coder_ready_for_review(coder_report):
                workspace_git.revert_snapshot(snapshot)
                return False, coder_report
            diff = git_diff_since(snapshot)
            action = "reviewer"
            optimizer_trace.append_event(
                run_log_path,
                {"event": "handoff_to_reviewer", "round": round_index, "diff": diff[:12000]},
            )
            continue

        if action == "reviewer":
            emit_phase(phase_sink, "reviewing")
            diff = git_diff_since(snapshot)
            reviewer_report = run_reviewer(
                api_key,
                model,
                task,
                reviewer_context(context, coder_report, diff),
                session=reviewer,
            )
        optimizer_trace.append_event(
            run_log_path,
            {
                "event": "reviewer_turn",
                "round": round_index,
                "report": reviewer_report,
                "diff": diff[:12000],
            },
        )
        if reviewer_review_passed(reviewer_report):
            workspace_git.commit_task("coder", task)
            optimizer_trace.append_event(
                run_log_path,
                {"event": "approved", "round": round_index},
            )
            return True, reviewer_report

        feedback = reviewer_report
        action = "coder"
        optimizer_trace.append_event(
            run_log_path,
            {"event": "handoff_to_coder", "round": round_index, "comment": feedback[:8000]},
        )

    workspace_git.revert_snapshot(snapshot)
    optimizer_trace.append_event(run_log_path, {"event": "max_rounds"})
    return False, reviewer_report or coder_report


def reviewer_context(context, coder_report, diff: str) -> str:
    parts = []
    if context:
        parts.append(context)
    parts.append("Coder summary:\n" + coder_report)
    if diff.strip():
        parts.append("Git diff:\n" + diff[:16000])
    else:
        parts.append("Git diff: (empty)")
    return "\n\n".join(parts)
