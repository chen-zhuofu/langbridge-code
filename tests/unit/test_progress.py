from unittest.mock import patch

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


def test_build_turn_user_content_includes_progress(tmp_path):
    run_log = tmp_path / "run.json"
    write_progress(run_log, PROGRESS_HEADER + "## Turn 1\n- Planned auth\n")
    content = build_turn_user_content(run_log, "continue")
    assert "Session progress from prior turns" in content
    assert "Planned auth" in content
    assert "Current request:\ncontinue" in content


def test_build_turn_user_content_includes_continuation_directive(tmp_path):
    run_log = tmp_path / "session.json"
    todo = (
        "<!-- task_type: slide -->\n# Plan\n\n"
        "- [x] Done step\n"
        "- [ ] 美化与校验：检查每页内容\n"
    )
    (tmp_path / "todo_list.md").write_text(todo, encoding="utf-8")
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


def test_build_turn_user_content_includes_recent_dialogue(tmp_path):
    run_log = tmp_path / "session.json"
    run_log.write_text(
        '{"summary": "", "turns": [{"turn_id": 1, "user": "build web", '
        '"assistant": "Built the landing page."}]}\n',
        encoding="utf-8",
    )
    content = build_turn_user_content(run_log, "add a footer")
    assert "Recent session dialogue" in content
    assert "build web" in content
    assert "Built the landing page" in content
    assert "Current request:\nadd a footer" in content
    assert "Current request:\nadd a footer" in content


def test_recent_session_dialogue_skips_in_progress_turn(tmp_path):
    from langbridge_code.util.session import recent_session_dialogue

    run_log = tmp_path / "session.json"
    run_log.write_text(
        '{"summary": "", "turns": ['
        '{"turn_id": 1, "user": "first", "assistant": "done"},'
        '{"turn_id": 2, "user": "second", "assistant": ""}'
        "]}\n",
        encoding="utf-8",
    )
    dialogue = recent_session_dialogue(run_log, limit=3)
    assert "first" in dialogue
    assert "second" not in dialogue


def test_create_artifact_session_layout(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    from langbridge_code.util.artifacts import create_artifact_session

    session_json = create_artifact_session("Fix login API")
    assert session_json.name == "session.json"
    assert session_json.parent.name.startswith("session-Fix-login-API-")
    assert (session_json.parent / "traces").is_dir()
    assert (session_json.parent / "debug").is_dir()


def test_build_main_agent_messages(tmp_path):
    messages = build_main_agent_messages(tmp_path / "run.json", "hi")
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "hi"}


def test_append_turn_progress_writes_file(tmp_path, monkeypatch):
    run_log = tmp_path / "session.json"
    run_log.write_text(
        '{"summary": "", "turns": [{"turn_id": 1, "user": "build auth", '
        '"assistant": "Planned.", "steps": [], "input": []}]}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "langbridge_code.util.progress._summarize_turn_progress",
        lambda *args, **kwargs: "## Turn 1\n- User asked for auth\n- Planner wrote todo",
    )
    append_turn_progress("key", "model", run_log, 1)
    text = read_progress(run_log)
    assert text.startswith(PROGRESS_HEADER)
    assert "Planner wrote todo" in text
    assert progress_path(run_log).name == "progress.md"


def test_append_turn_progress_appends_second_turn(tmp_path, monkeypatch):
    run_log = tmp_path / "session.json"
    write_progress(run_log, PROGRESS_HEADER + "## Turn 1\n- First\n")
    run_log.write_text(
        '{"summary": "", "turns": [{"turn_id": 2, "user": "continue", '
        '"assistant": "Done.", "steps": [], "input": []}]}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "langbridge_code.util.progress._summarize_turn_progress",
        lambda *args, **kwargs: "## Turn 2\n- Continued work",
    )
    append_turn_progress("key", "model", run_log, 2)
    text = read_progress(run_log)
    assert "First" in text
    assert "Continued work" in text


def test_append_turn_progress_stub_writes_immediately(tmp_path):
    run_log = tmp_path / "session.json"
    append_turn_progress_stub(run_log, 3, user="2", assistant="Done.")
    text = read_progress(run_log)
    assert "## Turn 3" in text
    assert "**In:** 2" in text
    assert "**Out:** Done." in text


def test_append_turn_progress_replace_turn(tmp_path, monkeypatch):
    run_log = tmp_path / "session.json"
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

    run_log = tmp_path / "session.json"
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
