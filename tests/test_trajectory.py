import langbridge_cli.trajectory as trajectory


def test_trajectory_writes_nothing_when_debug_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGBRIDGE_DEBUG_LLM", raising=False)
    run_log = tmp_path / "session.json"

    trajectory.write_trajectory_finish(run_log, "PM agent", 1, "done")

    assert not trajectory.trajectory_path(run_log).exists()


def test_trajectory_appends_human_readable_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM", "1")
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
    trajectory.write_trajectory_step(run_log, "L4 engineer", 2, 0, output)
    trajectory.write_trajectory_observation(
        run_log, "L4 engineer", 2, 0, {"call_id": "c1", "output": "file contents here"}
    )
    trajectory.write_trajectory_finish(run_log, "L4 engineer", 2, "L4_STATUS: READY_FOR_REVIEW")

    text = trajectory.trajectory_path(run_log).read_text(encoding="utf-8")
    assert "[L4 engineer] turn 2 · step 0" in text
    assert "Inspect repo." in text
    assert "read_file" in text
    assert "purpose: look at the file" in text
    assert '"path": "README.md"' in text
    assert "file contents here" in text
    assert "[L4 engineer] turn 2 · FINAL" in text
    assert "L4_STATUS: READY_FOR_REVIEW" in text
