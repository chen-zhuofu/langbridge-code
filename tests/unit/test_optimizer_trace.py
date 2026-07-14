from langbridge_code.training import optimizer_trace
from langbridge_code.training.optimizer_trace import (
    append_event,
    read_events,
    trace_to_loop_rounds,
    trace_to_loop_rounds_from_path,
)
from langbridge_code.util.trace_log import begin_trace


def _artifact_session(tmp_path, slug="test"):
    session_dir = tmp_path / f"session-{slug}-2026-07-09T120000"
    session_dir.mkdir()
    (session_dir / "traces").mkdir()
    (session_dir / "debug").mkdir()
    (session_dir / "progress.md").write_text("# Session progress\n", encoding="utf-8")
    return session_dir


def test_trace_to_loop_rounds_from_reviewer_events(tmp_path):
    run_log = _artifact_session(tmp_path)
    begin_trace(run_log, "2026-07-09T120000.00")
    append_event(run_log, {"event": "coder_turn", "report": "WORKER_STATUS: READY_FOR_REVIEW"})
    append_event(
        run_log,
        {"event": "reviewer_turn", "report": "REVIEW_VERDICT: NEEDS_WORK\nIssues: missing tests"},
    )
    append_event(
        run_log,
        {"event": "reviewer_turn", "report": "REVIEW_VERDICT: PASS\nEvidence: ok"},
    )

    parsed = trace_to_loop_rounds(run_log, "diff text")
    assert len(parsed["rounds"]) == 2
    assert parsed["rounds"][0]["approved"] is False
    assert parsed["rounds"][1]["approved"] is True
    assert parsed["rounds"][0]["diff"] == "diff text"

    file_path = str(optimizer_trace.trace_path(run_log))
    parsed2 = trace_to_loop_rounds_from_path(file_path, "final")
    assert len(parsed2["rounds"]) == 2
    assert read_events(run_log)
