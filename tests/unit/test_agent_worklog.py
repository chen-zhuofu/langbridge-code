import langbridge_cli.persistence.agent_worklog as agent_worklog


def test_worklog_writes_nothing_without_an_active_run():
    # No run_log_path means no active loop (e.g. unit tests) -> nothing is written.
    agent_worklog.write_worklog_finish(None, "PM agent", 1, "done")
    assert agent_worklog.worklog_path(None, "PM agent") is None


def test_worklog_appends_to_the_role_worklog(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.config.L4_WORKLOG_DIR", tmp_path)
    run_log = tmp_path / "session.json"

    output = [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Inspect repo."}]},
        {
            "type": "function_call",
            "name": "read_file",
            "call_id": "c1",
            "arguments": '{"purpose":"look at the file","path":"README.md"}',
        },
    ]
    agent_worklog.write_worklog_step(run_log, "L4 engineer", 2, 0, output)
    agent_worklog.write_worklog_observation(
        run_log, "L4 engineer", 2, 0, {"call_id": "c1", "output": "file contents here"}
    )
    agent_worklog.write_worklog_finish(run_log, "L4 engineer", 2, "L4_STATUS: READY_FOR_REVIEW")

    text = (tmp_path / "l4_worklog.md").read_text(encoding="utf-8")
    assert "[L4 engineer] turn 2 · step 0" in text
    assert "Inspect repo." in text
    assert "read_file" in text
    assert "purpose: look at the file" in text
    assert '"path": "README.md"' in text
    assert "file contents here" in text
    assert "[L4 engineer] turn 2 · FINAL" in text
    assert "L4_STATUS: READY_FOR_REVIEW" in text
