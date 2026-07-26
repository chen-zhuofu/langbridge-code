import json

from langbridge_code.util.progress import (
    PROGRESS_HEADER,
    append_progress_note,
    append_turn_progress,
    append_turn_progress_stub,
    maybe_compact_progress,
    read_progress,
    write_progress,
)
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


def test_append_progress_note_creates_and_appends(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    result = append_progress_note(run_log, 1, "Fixed the bug")
    assert "Fixed the bug" in result
    text = read_progress(run_log)
    assert "## Turn 1" in text
    assert "### Note\nFixed the bug" in text
    append_progress_note(run_log, 1, "Tests pass")
    text = read_progress(run_log)
    assert text.index("Fixed the bug") < text.index("Tests pass")


def test_append_progress_note_survives_stub_rewrite(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_progress_note(run_log, 2, "Committed the plan")
    append_turn_progress_stub(run_log, 2, user="do it", assistant="Done.")
    text = read_progress(run_log)
    assert "### Note\nCommitted the plan" in text
    assert "**Out:** Done." in text
    assert text.count("## Turn 2") == 1


def test_append_progress_note_survives_enrich(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_progress_note(run_log, 1, "Key decision recorded")
    monkeypatch.setattr(
        "langbridge_code.util.progress._summarize_turn_progress",
        lambda *args, **kwargs: "## Turn 1\n- enriched\n**Out:** done",
    )
    monkeypatch.setattr(
        "langbridge_code.util.progress.maybe_compact_progress",
        lambda *args, **kwargs: False,
    )
    append_turn_progress("key", "model", run_log, 1, replace_turn=True)
    text = read_progress(run_log)
    assert "enriched" in text
    assert "### Note\nKey decision recorded" in text


def test_maybe_compact_progress_merges_middle_turns(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    body = PROGRESS_HEADER
    for turn in range(1, 6):
        body += f"## Turn {turn}\n\n- did thing {turn} " + "z" * 200 + "\n\n"
    write_progress(run_log, body)

    monkeypatch.setattr(
        "langbridge_code.util.progress._merge_progress_sections_llm",
        lambda api_key, model, sections: (
            f"## Turns {sections[0].start}-{sections[-1].end}\n- merged middle work"
        ),
    )
    import langbridge_code.util.progress as progress_mod

    monkeypatch.setattr(
        progress_mod, "PROGRESS_MAX_FRACTION", 0.3, raising=False
    )
    # Force a tiny window so the file is over budget.
    monkeypatch.setattr(
        "langbridge_code.llm.model_context.model_context_window",
        lambda model: 500,
    )
    changed = maybe_compact_progress("key", "model", run_log)
    assert changed
    text = read_progress(run_log)
    assert "## Turn 1" in text
    assert "## Turns 2-4" in text
    assert "## Turn 5" in text
    assert "- merged middle work" in text
    assert "did thing 3" not in text
    records = [
        json.loads(line)
        for line in (run_log / "compactions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records[0]["type"] == "progress_compaction"
    if "full_event_attachment" in records[0]:
        attachment = run_log / records[0]["full_event_attachment"]
        full_event = json.loads(attachment.read_text(encoding="utf-8"))
    else:
        full_event = records[0]
    assert "did thing 3" in full_event["input"]["progress_markdown"]
    assert "merged middle work" in full_event["output"]["progress_markdown"]


def test_maybe_compact_progress_noop_under_budget(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress(run_log, PROGRESS_HEADER + "## Turn 1\n- small\n")
    assert maybe_compact_progress("key", "kimi-k2.7-code", run_log) is False
    assert "## Turn 1" in read_progress(run_log)


def test_maybe_compact_progress_preserves_goal_block(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    body = PROGRESS_HEADER + "## Goal\n- **Condition:** ship it\n- **Status:** active\n\n"
    for turn in range(1, 5):
        body += f"## Turn {turn}\n\n- work {turn} " + "w" * 200 + "\n\n"
    write_progress(run_log, body)
    monkeypatch.setattr(
        "langbridge_code.util.progress._merge_progress_sections_llm",
        lambda api_key, model, sections: (
            f"## Turns {sections[0].start}-{sections[-1].end}\n- merged"
        ),
    )
    monkeypatch.setattr(
        "langbridge_code.llm.model_context.model_context_window",
        lambda model: 400,
    )
    assert maybe_compact_progress("key", "model", run_log)
    text = read_progress(run_log)
    assert "- **Condition:** ship it" in text
    assert "## Turns 2-3" in text
