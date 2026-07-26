from langbridge_code.util.goal import SessionGoal
from langbridge_code.util.progress import (
    PROGRESS_HEADER,
    parse_goal_block,
    read_progress,
    remove_goal_block,
    upsert_goal_block,
    write_progress,
    write_progress_note,
)


def test_write_progress_round_trip(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress(run_log, PROGRESS_HEADER + "#### Key discoveries\n- Planned auth\n")
    assert "Planned auth" in read_progress(run_log)


def test_write_progress_note_overrides_previous(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress_note(run_log, "#### Key discoveries\n- first")
    write_progress_note(run_log, "#### Key discoveries\n- second")
    text = read_progress(run_log)
    assert "second" in text
    assert "first" not in text
    assert text.count("#### Key discoveries") == 1


def test_write_progress_note_preserves_goal(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    upsert_goal_block(
        run_log,
        SessionGoal(condition="ship it", status="active", turn_count=1),
    )
    write_progress_note(run_log, "#### Next\n- keep going")
    text = read_progress(run_log)
    assert "## Goal" in text
    assert "ship it" in text
    assert "#### Next" in text
    goal = parse_goal_block(text)
    assert goal is not None
    assert goal.condition == "ship it"


def test_write_progress_note_sets_progress_boundary(tmp_path):
    from langbridge_code.util.session_traces import read_traces

    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress_note(run_log, "#### Work done\n- did it", turn_id=3)
    assert "## Progress boundary (turn 3)" in read_traces(run_log)


def test_plain_text_note_survives_goal_upsert(tmp_path):
    """A note without a #### heading must not be swallowed by goal rewrites."""
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress_note(run_log, "just plain facts, no heading")
    upsert_goal_block(
        run_log,
        SessionGoal(condition="ship it", status="active", turn_count=1),
    )
    text = read_progress(run_log)
    assert "just plain facts, no heading" in text
    assert "ship it" in text
    # Second upsert (goal loop runs every round) must still keep the note.
    upsert_goal_block(
        run_log,
        SessionGoal(condition="ship it", status="active", turn_count=2),
    )
    assert "just plain facts, no heading" in read_progress(run_log)


def test_remove_goal_block_keeps_note(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    upsert_goal_block(
        run_log,
        SessionGoal(condition="ship it", status="active", turn_count=1),
    )
    write_progress_note(run_log, "#### Work done\n- shipped")
    remove_goal_block(run_log)
    text = read_progress(run_log)
    assert "## Goal" not in text
    assert "shipped" in text
