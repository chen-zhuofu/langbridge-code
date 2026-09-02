"""Per-agent session artifact read ACL."""
import pytest

from langbridge_code.agents.common.workspace import (
    configure_agent_artifacts,
    nested_agent_artifacts,
    set_workspace_root,
)
from langbridge_code.tools.execution import prepare_execution_output
from langbridge_code.tools.filesystem import read_file
from langbridge_code.util.artifacts import (
    explorer_report_path,
    task_attachments_dir,
    task_progress_path,
)


@pytest.fixture
def session(tmp_path):
    set_workspace_root(tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "session_memory.md").write_text("# Session memory\n", encoding="utf-8")
    (session_dir / "traces.md").write_text("# Session traces\nsecret\n", encoding="utf-8")
    (session_dir / "session.md").write_text("# session\n", encoding="utf-8")
    yield session_dir
    set_workspace_root(None)
    configure_agent_artifacts(None, label="LangBridge")


def test_main_reads_task_progress_and_reports_not_traces(session, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.tools.execution.MAX_EXECUTION_OUTPUT_CHARS", 50
    )
    worker_progress = task_progress_path(session, "task-a", role="Worker")
    worker_progress.parent.mkdir(parents=True)
    worker_progress.write_text("# Session memory\n\nworker note\n", encoding="utf-8")
    report = explorer_report_path(session, "task-a", 0)
    report.parent.mkdir(parents=True)
    report.write_text("## Answer\nok\n", encoding="utf-8")
    configure_agent_artifacts(session, label="LangBridge")

    assert "worker note" in read_file(str(worker_progress))
    assert "ok" in read_file(str(report))
    with pytest.raises(ValueError):
        read_file(str(session / "traces.md"))
    with pytest.raises(ValueError):
        read_file(str(session / "session.md"))


def test_worker_cannot_read_main_spill_or_reviewer_progress(session, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.tools.execution.MAX_EXECUTION_OUTPUT_CHARS", 50
    )
    configure_agent_artifacts(session, label="LangBridge")
    _, _, main_spill = prepare_execution_output("m" * 120, run_log_path=session)
    assert main_spill is not None

    reviewer_progress = task_progress_path(session, "task-a", role="Reviewer")
    reviewer_progress.parent.mkdir(parents=True)
    reviewer_progress.write_text("# Session memory\n\nreviewer only\n", encoding="utf-8")

    configure_agent_artifacts(session, label="Worker", task_name="task-a")
    with pytest.raises(ValueError):
        read_file(str(main_spill))
    with pytest.raises(ValueError):
        read_file(str(reviewer_progress))

    # Own spill is readable.
    _, _, worker_spill = prepare_execution_output("w" * 120, run_log_path=session)
    assert worker_spill.parent == task_attachments_dir(
        session, "task-a", "Worker"
    ).resolve()
    assert "www" in read_file(str(worker_spill), offset=1, limit=1)


def test_nested_agent_artifacts_restores_parent(session):
    configure_agent_artifacts(session, label="LangBridge")
    progress = session / "session_memory.md"
    assert "Session memory" in read_file(str(progress))

    with nested_agent_artifacts(session, label="Worker", task_name="task-a"):
        with pytest.raises(ValueError):
            read_file(str(progress))

    assert "Session memory" in read_file(str(progress))
