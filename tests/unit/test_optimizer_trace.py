from langbridge_code.workflow import optimizer_trace
from langbridge_code.workflow.optimizer_trace import (
    append_event,
    read_events,
    trace_to_loop_rounds,
    trace_to_loop_rounds_from_path,
)


def test_trace_to_loop_rounds_from_reviewer_events(tmp_path):
    run_log = tmp_path / "session.json"
    append_event(run_log, {"event": "coder_turn", "report": "CODER_STATUS: READY_FOR_REVIEW"})
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
