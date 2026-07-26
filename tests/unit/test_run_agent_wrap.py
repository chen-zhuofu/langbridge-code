"""Tests for eval run_agent task wrapping."""
from langbridge_eval.run_agent import wrap_issue_as_task


def test_wrap_issue_as_task_requires_code_changes():
    issue = (
        "Variables no longer loaded.\n\n"
        "### Questions\n\n"
        "* Is this a bug or a new design direction?\n"
    )
    task = wrap_issue_as_task(issue)
    assert "<issue>" in task
    assert "Variables no longer loaded." in task
    assert "Implement the minimal code changes" in task
    assert "Do not only explain" in task
    assert "Is this a bug" in task


def test_wrap_issue_as_task_empty():
    assert wrap_issue_as_task("") == ""
    assert wrap_issue_as_task("   ") == ""
