import json

from langbridge_code.util.logging import read_turn_record, write_turn_complete
from langbridge_code.util.progress import (
    PROGRESS_HEADER,
    finalize_main_agent_turn,
    read_progress,
)
from langbridge_code.util.session import last_completed_turn_id, last_turn_id


def test_last_completed_turn_id_ignores_in_flight_turns():
    records = [
        {"turn_id": 7, "user": "a", "assistant": "done"},
        {"turn_id": 8, "user": "b", "assistant": ""},
        {"turn_id": 18, "user": "c", "assistant": "timeout"},
    ]
    assert last_turn_id(records) == 18
    assert last_completed_turn_id(records) == 18


def test_last_completed_turn_id_skips_gap_from_orphan_starts():
    records = [
        {"turn_id": 7, "user": "a", "assistant": "done"},
        *[{"turn_id": n, "user": "x", "assistant": ""} for n in range(8, 18)],
    ]
    assert last_completed_turn_id(records) == 7


def test_finalize_main_agent_turn_writes_session_and_progress_stub(tmp_path, monkeypatch):
    run_log = tmp_path / "session.json"
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

    record = read_turn_record(run_log, 8)
    assert record["user"] == "build tanks"
    assert record["assistant"] == "Stopped by user."

    progress = read_progress(run_log)
    assert "## Turn 8" in progress
    assert "**In:** build tanks" in progress
    assert "**Out:** Stopped by user." in progress


def test_finalize_main_agent_turn_defaults_empty_outcome(tmp_path, monkeypatch):
    run_log = tmp_path / "session.json"
    monkeypatch.setattr(
        "langbridge_code.util.progress.schedule_append_turn_progress",
        lambda *args, **kwargs: None,
    )

    finalize_main_agent_turn("key", "model", run_log, 3, user="hi", assistant="")

    record = read_turn_record(run_log, 3)
    assert record["assistant"] == "(turn ended without a reply)"


def test_write_turn_complete_persists_both_sides(tmp_path):
    run_log = tmp_path / "session.json"
    write_turn_complete(run_log, 8, "hello", "world")

    data = json.loads(run_log.read_text(encoding="utf-8"))
    assert data["turns"][0]["turn_id"] == 8
