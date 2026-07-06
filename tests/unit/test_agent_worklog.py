import langbridge_code.persistence.agent_worklog as agent_worklog


def test_worklog_writes_nothing_without_an_active_run():
    # No run_log_path means no active loop (e.g. unit tests) -> nothing is written.
    agent_worklog.write_worklog_finish(None, "PM agent", 1, 1, "done")
    assert agent_worklog.worklog_path(None, "PM agent") is None
    assert agent_worklog.new_worklog_id(None, "PM agent") is None


def test_worklog_appends_to_one_instance_file(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.settings.CODER_WORKLOG_DIR", tmp_path)
    run_log = tmp_path / "session.json"
    instance_id = 1

    output = [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Inspect repo."}]},
        {
            "type": "function_call",
            "name": "read_file",
            "call_id": "c1",
            "arguments": '{"purpose":"look at the file","path":"README.md"}',
        },
    ]
    agent_worklog.write_worklog_step(run_log, "Coder", instance_id, 2, 0, output)
    agent_worklog.write_worklog_observation(
        run_log, "Coder", instance_id, 2, 0, {"call_id": "c1", "output": "file contents here"}
    )
    agent_worklog.write_worklog_finish(run_log, "Coder", instance_id, 2, "CODER_STATUS: READY_FOR_REVIEW")

    text = (tmp_path / "session" / "coder_1.md").read_text(encoding="utf-8")
    assert "[Coder] turn 2 · step 0" in text
    assert "Inspect repo." in text
    assert "read_file" in text
    assert "purpose: look at the file" in text
    assert '"path": "README.md"' in text
    assert "file contents here" in text
    assert "[Coder] turn 2 · FINAL" in text
    assert "CODER_STATUS: READY_FOR_REVIEW" in text


def test_distinct_instances_get_distinct_files(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.settings.REVIEWER_WORKLOG_DIR", tmp_path)
    run_log = tmp_path / "session.json"

    first = agent_worklog.new_worklog_id(run_log, "Reviewer")
    second = agent_worklog.new_worklog_id(run_log, "Reviewer")
    assert first != second

    agent_worklog.write_worklog_finish(run_log, "Reviewer", first, 1, "REVIEW_VERDICT: NEEDS_WORK")
    agent_worklog.write_worklog_finish(run_log, "Reviewer", second, 1, "REVIEW_VERDICT: PASS")

    run_dir = tmp_path / "session"
    assert (run_dir / f"reviewer_{first}.md").read_text(encoding="utf-8").count("FINAL") == 1
    assert (run_dir / f"reviewer_{second}.md").read_text(encoding="utf-8").count("FINAL") == 1
    assert {p.name for p in run_dir.glob("reviewer_*.md")} == {f"reviewer_{first}.md", f"reviewer_{second}.md"}
