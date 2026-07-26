import langbridge_code.util.agent_worklog as agent_worklog
from langbridge_code.util.artifacts import create_artifact_session, session_trace_path
from langbridge_code.util.trace_log import begin_trace, end_trace


def test_worklog_writes_nothing_without_an_active_run():
    agent_worklog.write_worklog_finish(None, "Worker", 1, 1, "done")
    assert agent_worklog.new_worklog_id(None, "Worker") is None


def test_worklog_writes_to_unified_trace(tmp_path, monkeypatch):
    from langbridge_code.llm.parse import print_step_trace
    from langbridge_code.util.trace_log import trace_sink

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    run_log = create_artifact_session("Fix login bug")
    trace_id = "2026-07-09T120000.00"
    begin_trace(run_log, trace_id)

    output = [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Inspect repo."}]},
        {
            "type": "function_call",
            "name": "read_file",
            "call_id": "c1",
            "arguments": '{"description":"look at the file","path":"README.md"}',
        },
    ]
    instance_id = agent_worklog.new_worklog_id(run_log, "Worker")
    agent_worklog.write_worklog_received(run_log, "Worker", instance_id, 2, "Build auth")
    print_step_trace(output, include_message=True, label="Worker", sink=trace_sink)
    agent_worklog.write_worklog_observation(
        run_log, "Worker", instance_id, 2, 0, {"call_id": "c1", "output": "file contents here"}
    )
    agent_worklog.write_worklog_finish(run_log, "Worker", instance_id, 2, "WORKER_STATUS: READY_FOR_REVIEW")
    end_trace()

    text = session_trace_path(run_log).read_text(encoding="utf-8")
    assert f"## {trace_id}" in text
    assert "Worker" in text
    assert "Inspect repo." in text
    assert "read_file" in text
    assert "READY_FOR_REVIEW" in text
    assert text.count("think: Inspect repo.") == 1
    assert text.count("→ read_file(") == 1

def test_distinct_instances_get_distinct_ids(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    run_log = create_artifact_session("Review task")
    first = agent_worklog.new_worklog_id(run_log, "Reviewer")
    second = agent_worklog.new_worklog_id(run_log, "Reviewer")
    assert first != second


def test_trace_entries_keep_full_text(tmp_path, monkeypatch):
    from langbridge_code.util.trace_log import write_line

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    run_log = create_artifact_session("Full text")
    begin_trace(run_log, "2026-07-09T120000.00")
    long_reply = "多行回复第一行\n第二行内容不截断" + " padding" * 30
    write_line("LangBridge", long_reply)
    end_trace()

    text = session_trace_path(run_log).read_text(encoding="utf-8")
    assert "第二行内容不截断" in text
    assert "..." not in text


def test_trace_oversized_entry_moves_to_attachment(tmp_path, monkeypatch):
    from langbridge_code.util.trace_log import write_line

    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    run_log = create_artifact_session("Huge output")
    begin_trace(run_log, "2026-07-09T120000.00")
    huge = "x" * 10_000
    write_line("LangBridge", huge)
    end_trace()

    text = session_trace_path(run_log).read_text(encoding="utf-8")
    assert huge not in text
    assert "[full text](attachments/" in text
    attachments = list((run_log / "attachments").iterdir())
    assert len(attachments) == 1
    assert attachments[0].read_text(encoding="utf-8") == huge
