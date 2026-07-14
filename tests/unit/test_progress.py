from langbridge_code.util.progress import (
    PROGRESS_HEADER,
    append_turn_progress,
    append_turn_progress_stub,
    build_main_agent_messages,
    build_turn_user_content,
    progress_path,
    read_progress,
    schedule_append_turn_progress,
    write_progress,
)


def test_build_turn_user_content_without_progress():
    assert build_turn_user_content(None, "hello") == "hello"


def test_build_turn_user_content_never_inlines_progress(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress(run_log, PROGRESS_HEADER + "## Turn 1\n- Planned auth\n")
    content = build_turn_user_content(run_log, "continue the auth work")
    assert content == "continue the auth work"
    assert "Planned auth" not in content


def test_build_turn_user_content_includes_continuation_directive(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    todo = (
        "<!-- task_type: slide -->\n# Plan\n\n"
        "- [x] Done step\n"
        "- [ ] 美化与校验：检查每页内容\n"
    )
    (run_log / "todo_list.md").write_text(todo, encoding="utf-8")
    content = build_turn_user_content(run_log, "继续")
    assert "Continuation directive" in content
    assert "美化与校验" in content
    assert "Do NOT use ask_user" in content


def test_is_continuation_request():
    from langbridge_code.agents.common.todo_list import is_continuation_request

    assert is_continuation_request("继续")
    assert is_continuation_request("继续？")
    assert is_continuation_request("continue")
    assert not is_continuation_request("继续开发游戏")
    assert not is_continuation_request("做ppt")


def test_create_artifact_session_layout(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    from langbridge_code.util.artifacts import create_artifact_session

    session_dir = create_artifact_session("Fix login API")
    assert session_dir.is_dir()
    assert session_dir.name.startswith("session-Fix-login-API-")
    assert (session_dir / "traces").is_dir()
    assert (session_dir / "debug").is_dir()
    assert (session_dir / "progress.md").is_file()
    assert (session_dir / "traces.md").is_file()
    assert not (session_dir / "session.json").exists()


def test_build_main_agent_messages(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    messages = build_main_agent_messages(run_log, "hi")
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "hi"}


def test_append_turn_progress_writes_file(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()

    monkeypatch.setattr(
        "langbridge_code.util.progress._summarize_turn_progress",
        lambda *args, **kwargs: "## Turn 1\n- User asked for auth\n- Planner wrote todo",
    )
    append_turn_progress(
        "key", "model", run_log, 1, user="build auth", assistant="Planned."
    )
    text = read_progress(run_log)
    assert text.startswith(PROGRESS_HEADER)
    assert "Planner wrote todo" in text
    assert progress_path(run_log).name == "progress.md"


def test_append_turn_progress_appends_second_turn(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress(run_log, PROGRESS_HEADER + "## Turn 1\n- First\n")
    monkeypatch.setattr(
        "langbridge_code.util.progress._summarize_turn_progress",
        lambda *args, **kwargs: "## Turn 2\n- Continued work",
    )
    append_turn_progress(
        "key", "model", run_log, 2, user="continue", assistant="Done."
    )
    text = read_progress(run_log)
    assert "First" in text
    assert "Continued work" in text


def test_append_turn_progress_stub_writes_immediately(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_turn_progress_stub(run_log, 3, user="2", assistant="Done.")
    text = read_progress(run_log)
    assert "## Turn 3" in text
    assert "**In:** 2" in text
    assert "**Out:** Done." in text


def test_append_turn_progress_replace_turn(tmp_path, monkeypatch):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    write_progress(run_log, PROGRESS_HEADER + "## Turn 1\n**In:** stub\n")
    monkeypatch.setattr(
        "langbridge_code.util.progress._summarize_turn_progress",
        lambda *args, **kwargs: "## Turn 1\n- enriched summary\n**In:** stub\n**Out:** done",
    )
    append_turn_progress("key", "model", run_log, 1, replace_turn=True)
    text = read_progress(run_log)
    assert "enriched summary" in text
    assert text.count("## Turn 1") == 1


def test_schedule_append_turn_progress_enriches_stub(tmp_path, monkeypatch):
    import time

    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_turn_progress_stub(run_log, 1, user="hi", assistant="hello")
    monkeypatch.setattr(
        "langbridge_code.util.progress._summarize_turn_progress",
        lambda *args, **kwargs: "## Turn 1\n- enriched\n**In:** hi\n**Out:** hello",
    )
    schedule_append_turn_progress("key", "model", run_log, 1, user="hi", assistant="hello")
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if "enriched" in read_progress(run_log):
            break
        time.sleep(0.02)
    assert "enriched" in read_progress(run_log)
