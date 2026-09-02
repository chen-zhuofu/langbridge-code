from langbridge_code.util import optimizer_trace
from langbridge_code.util.optimizer_trace import append_event
from langbridge_code.util.trace_log import begin_trace


def _artifact_session(tmp_path, slug="test"):
    session_dir = tmp_path / f"session-{slug}-2026-07-09T120000"
    session_dir.mkdir()
    (session_dir / "debug").mkdir()
    (session_dir / "session_memory.md").write_text("# Session memory\n", encoding="utf-8")
    return session_dir


def test_append_event_writes_trace_lines(tmp_path):
    run_log = _artifact_session(tmp_path)
    begin_trace(run_log, "2026-07-09T120000.00")
    append_event(run_log, {"event": "coder_turn", "report": "WORKER_STATUS: READY_FOR_REVIEW"})
    path = optimizer_trace.trace_path(run_log)
    assert path is not None and path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "optimizer" in text
    assert "coder_turn" in text
    assert "READY_FOR_REVIEW" in text
