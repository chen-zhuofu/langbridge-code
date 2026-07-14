from langbridge_code.util.progress import (
    PROGRESS_HEADER,
    finalize_main_agent_turn,
    last_progress_turn_id,
    read_progress,
    write_progress,
)
from langbridge_code.util.session import last_turn_id


def test_last_progress_turn_id_reads_progress_headers(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress(
        run_log,
        PROGRESS_HEADER
        + "## Turn 7\n\n**In:** a\n\n## Turn 18\n\n**In:** c\n",
    )
    assert last_progress_turn_id(run_log) == 18
    assert last_turn_id(run_log) == 18


def test_last_progress_turn_id_empty_session(tmp_path):
    run_log = tmp_path / "session-empty"
    run_log.mkdir()
    assert last_progress_turn_id(run_log) == 0
    assert last_turn_id(run_log) == 0


def test_finalize_main_agent_turn_writes_progress_stub(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    monkeypatch.setattr(
        "langbridge_code.util.progress.schedule_append_turn_progress",
        lambda *args, **kwargs: None,
    )

    finalize_main_agent_turn(
        "key",
        "model",
        run_log,
        8,
        user="build tanks",
        assistant="Stopped by user.",
    )

    progress = read_progress(run_log)
    assert "## Turn 8" in progress
    assert "**In:** build tanks" in progress
    assert "**Out:** Stopped by user." in progress


def test_finalize_main_agent_turn_defaults_empty_outcome(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    monkeypatch.setattr(
        "langbridge_code.util.progress.schedule_append_turn_progress",
        lambda *args, **kwargs: None,
    )

    finalize_main_agent_turn("key", "model", run_log, 3, user="hi", assistant="")

    progress = read_progress(run_log)
    assert "**Out:** (turn ended without a reply)" in progress
