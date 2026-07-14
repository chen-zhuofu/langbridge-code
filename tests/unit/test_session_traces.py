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
    read_traces,
    select_traces_for_resume,
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


def test_select_traces_full_when_fits(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(run_log, 1, _round(user="hi", assistant="done"))
    selected = select_traces_for_resume(run_log, model="kimi-k2.7-code", progress="")
    assert '"hi"' in selected


def test_select_traces_after_boundary_when_large(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(run_log, 1, _round(user="old turn " + "x" * 2000, assistant="old reply"))
    append_progress_boundary(run_log, 1)
    append_raw_round(run_log, 2, _round(user="new turn", assistant="new reply"))
    # Tiny window: full file cannot fit, post-boundary can.
    monkeypatch.setattr(
        "langbridge_code.util.session_traces.model_context_window",
        lambda model: 600,
    )
    selected = select_traces_for_resume(run_log, model="tiny", progress="")
    assert "new turn" in selected
    assert "old reply" not in selected


def test_select_traces_head_trim_keeps_newest(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    for index in range(6):
        append_raw_round(run_log, 1, _round(assistant=f"round {index} " + "y" * 200))
    monkeypatch.setattr(
        "langbridge_code.util.session_traces.model_context_window",
        lambda model: 500,
    )
    selected = select_traces_for_resume(run_log, model="tiny", progress="")
    assert selected
    assert "round 5" in selected
    assert "round 0" not in selected


def test_select_traces_empty_file(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    assert select_traces_for_resume(run_log, model="kimi-k2.7-code", progress="") == ""


def test_agent_trace_jsonl_per_instance(tmp_path):
    from langbridge_code.util.session_traces import (
        agent_trace_path,
        append_agent_trace_round,
        read_agent_trace,
    )

    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_agent_trace_round(
        run_log,
        "Worker",
        1,
        3,
        [{"role": "system", "content": "sys"}, *_round(user="do it", assistant="did it")],
        step=2,
    )
    append_agent_trace_round(run_log, "Worker", 2, 3, _round(assistant="other instance"))

    path_one = agent_trace_path(run_log, "Worker", 1)
    assert path_one.name == "worker_1.trace.jsonl"
    assert path_one.parent.name == "traces"

    records = read_agent_trace(run_log, "Worker", 1)
    assert len(records) == 1
    assert records[0]["turn"] == 3
    assert records[0]["step"] == 2
    assert all(m.get("role") != "system" for m in records[0]["messages"])
    assert records[0]["messages"][0]["content"] == "do it"

    other = read_agent_trace(run_log, "Worker", 2)
    assert len(other) == 1
    assert other[0]["messages"][0]["content"] == "other instance"


def test_finish_step_writes_agent_trace_for_any_label(tmp_path):
    from langbridge_code.context.agent_context import finish_step, init_agent_context
    from langbridge_code.util.session_traces import read_agent_trace

    run_log = tmp_path / "session-demo"
    run_log.mkdir()

    class FakeSession:
        api_key = None
        model = None
        label = "Reviewer"
        run_log_path = run_log
        turn_id = 1
        step = 0

    session = FakeSession()
    messages, context, worklog_id = init_agent_context(
        system_prompt="sys", run_log_path=run_log, label="Reviewer"
    )
    session.worklog_id = worklog_id
    context.begin_turn("review this")
    finish_step(context, [{"role": "assistant", "content": "looks good"}], session, 1000)

    records = read_agent_trace(run_log, "Reviewer", worklog_id)
    assert len(records) == 1
    contents = [m.get("content") for m in records[0]["messages"]]
    assert "review this" in contents
    assert "looks good" in contents


def test_append_progress_note_creates_and_appends(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    result = append_progress_note(run_log, 1, "Fixed the bug")
    assert "Fixed the bug" in result
    text = read_progress(run_log)
    assert "## Turn 1" in text
    assert "- **Note:** Fixed the bug" in text
    append_progress_note(run_log, 1, "Tests pass")
    text = read_progress(run_log)
    assert text.index("Fixed the bug") < text.index("Tests pass")


def test_append_progress_note_survives_stub_rewrite(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_progress_note(run_log, 2, "Committed the plan")
    append_turn_progress_stub(run_log, 2, user="do it", assistant="Done.")
    text = read_progress(run_log)
    assert "- **Note:** Committed the plan" in text
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
    assert "- **Note:** Key decision recorded" in text


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
