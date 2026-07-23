"""Failed worker loops keep partial work in the tree (no auto-revert)."""
import subprocess

import pytest

from langbridge_code.tools import agent_worker_reviewer as awr
from langbridge_code.tools.agent_worker_reviewer import (
    StepOutcome,
    partial_work_note,
    run_worker_reviewer_loop,
)


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git("init", cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-m", "init", cwd=tmp_path)
    return tmp_path


def test_partial_work_note_lists_dirty_and_untracked(repo):
    snapshot = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    (repo / "app.py").write_text("print('v2')\n", encoding="utf-8")
    (repo / "new_module.py").write_text("x = 1\n", encoding="utf-8")

    note = partial_work_note(snapshot, repo)

    assert "not reverted" in note
    assert "app.py" in note
    assert "new_module.py" in note
    assert "re-dispatch" in note and "revise todo_list.md" in note


def test_partial_work_note_empty_when_tree_clean(repo):
    snapshot = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert partial_work_note(snapshot, repo) == ""


class _StubSession:
    """Worker stub: edits a file then reports not-ready; reviewer stub unused."""

    def __init__(self, repo, final_text):
        self.repo = repo
        self.final_text = final_text

    def begin_send(self, *args, **kwargs):
        pass

    def _schedule_memory_if_needed(self):
        pass

    def run_one_step(self, loop_budget=None):
        (self.repo / "app.py").write_text("print('half done')\n", encoding="utf-8")
        return StepOutcome.FINAL, self.final_text


def test_failed_loop_keeps_partial_work_and_reports_it(repo, monkeypatch):
    monkeypatch.setattr(
        awr,
        "new_worker_session",
        lambda *a, **k: _StubSession(repo, "WORKER_STATUS: IN_PROGRESS\nStuck."),
    )
    monkeypatch.setattr(awr, "new_reviewer_session", lambda *a, **k: _StubSession(repo, ""))
    monkeypatch.setattr(awr, "append_event", lambda *a, **k: None)

    passed, report = run_worker_reviewer_loop(
        "key",
        "model",
        "Fix app",
        task_type="coding",
        cwd=repo,
    )

    assert passed is False
    # Half-done edit survives the failure.
    assert (repo / "app.py").read_text(encoding="utf-8") == "print('half done')\n"
    assert "Partial work left in the working tree" in report
    assert "app.py" in report


def test_revert_snapshot_hook_removed():
    assert not hasattr(awr, "revert_snapshot")
