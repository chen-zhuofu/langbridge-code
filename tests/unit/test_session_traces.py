from langbridge_code.util.session_traces import (
    TRACES_HEADER,
    append_progress_boundary,
    append_raw_round,
    build_resume_background,
    read_conversation,
    read_traces,
)


def _round(user=None, assistant=None):
    items = []
    if user:
        items.append({"role": "user", "content": user})
    if assistant:
        items.append({"role": "assistant", "content": assistant})
    return items


def test_append_raw_round_creates_file_and_strips_system(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(
        run_log,
        1,
        [{"role": "system", "content": "sys"}, *_round(user="hi", assistant="hello")],
    )
    text = read_traces(run_log)
    assert text.startswith(TRACES_HEADER)
    assert "## Turn 1" in text
    assert '"hi"' in text
    assert "sys" not in text


def test_append_raw_round_groups_same_turn(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(run_log, 1, _round(user="hi", assistant="step one"))
    append_raw_round(run_log, 1, _round(assistant="step two"))
    append_raw_round(run_log, 2, _round(user="next", assistant="reply"))
    text = read_traces(run_log)
    assert text.count("## Turn 1") == 1
    assert text.count("## Turn 2") == 1
    assert text.index("step one") < text.index("step two") < text.index('"next"')


def test_read_conversation_returns_user_and_assistant_in_order(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(
        run_log,
        1,
        [
            {"role": "user", "content": "hi"},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think"}]},
            {"type": "function_call", "name": "read_file", "call_id": "c1", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "data"},
            {"role": "assistant", "content": "hello"},
        ],
    )
    append_raw_round(run_log, 2, _round(user="next", assistant="reply"))
    conversation = read_conversation(run_log)
    assert conversation == [
        ("user", "hi"),
        ("assistant", "hello"),
        ("user", "next"),
        ("assistant", "reply"),
    ]


def test_read_conversation_handles_content_part_lists(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(
        run_log,
        1,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "output_text", "text": "part reply"}]},
        ],
    )
    assert read_conversation(run_log) == [("user", "hi"), ("assistant", "part reply")]


def test_read_conversation_hides_background_tool_result_events(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(
        run_log,
        1,
        [
            {
                "role": "user",
                "content": "<background_tool_results>\nworker completed\n</background_tool_results>",
            },
            {"role": "assistant", "content": "merged worker result"},
        ],
    )

    assert read_conversation(run_log) == [("assistant", "merged worker result")]


def test_read_conversation_empty_traces(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    assert read_conversation(run_log) == []


def test_append_progress_boundary_marks_turn(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(run_log, 1, _round(user="hi", assistant="done"))
    append_progress_boundary(run_log, 1)
    text = read_traces(run_log)
    assert "## Progress boundary (turn 1)" in text
    # Boundary is idempotent per turn tail.
    append_progress_boundary(run_log, 1)
    assert read_traces(run_log).count("## Progress boundary (turn 1)") == 1


def test_resume_background_full_traces_replace_progress_when_fits(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(run_log, 1, _round(user="hi", assistant="done"))
    background = build_resume_background(
        run_log, model="kimi-k2.7-code", progress="## Turn 1\n- summarized"
    )
    assert '"hi"' in background
    # Full raw traces fit, so the progress summary is redundant and dropped.
    assert "summarized" not in background


def test_resume_background_progress_plus_post_boundary_when_large(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(run_log, 1, _round(user="old turn " + "x" * 2000, assistant="old reply"))
    append_progress_boundary(run_log, 1)
    append_raw_round(run_log, 2, _round(user="new turn", assistant="new reply"))
    # Tiny window: full file cannot fit, progress + post-boundary can.
    monkeypatch.setattr(
        "langbridge_code.util.session_traces.model_context_window",
        lambda model: 600,
    )
    background = build_resume_background(
        run_log, model="tiny", progress="## Turn 1\n- old turn summarized"
    )
    assert "old turn summarized" in background
    assert "new turn" in background
    assert "old reply" not in background


def test_resume_background_head_trim_keeps_newest(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    for index in range(6):
        append_raw_round(run_log, 1, _round(assistant=f"round {index} " + "y" * 200))
    monkeypatch.setattr(
        "langbridge_code.util.session_traces.model_context_window",
        lambda model: 500,
    )
    background = build_resume_background(run_log, model="tiny", progress="")
    assert background
    assert "round 5" in background
    assert "round 0" not in background


def test_resume_background_empty_traces_returns_progress(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    assert build_resume_background(run_log, model="kimi-k2.7-code", progress="") == ""
    assert (
        build_resume_background(run_log, model="kimi-k2.7-code", progress="## Turn 1\n- note")
        == "## Turn 1\n- note"
    )

