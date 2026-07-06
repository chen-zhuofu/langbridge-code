"""Functional tests for the flat workflow (mock LLM boundaries only)."""

import json

from langbridge_code.workflow.run import run_workflow


def _write_todo(run_log_path, lines):
    content = "# Todo\n\n" + "\n".join(lines) + "\n"
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    (run_log_path.parent / f"{run_log_path.stem}.todo_list.md").write_text(content, encoding="utf-8")


def test_workflow_chat_reply_short_circuits(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"

    monkeypatch.setattr(
        "langbridge_code.workflow.run.route",
        lambda *args, **kwargs: {
            "kind": "chat",
            "reply": "Hello from LangBridge Code.",
            "hard": False,
            "task_type": "coding",
            "task_summary": "",
        },
    )

    reply = run_workflow("key", "model", "hi", run_log, 1, print_reply=False)
    assert reply == "Hello from LangBridge Code."


def test_workflow_easy_task_runs_coder_reviewer(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    calls = []

    monkeypatch.setattr(
        "langbridge_code.workflow.run.route",
        lambda *args, **kwargs: {
            "kind": "task",
            "reply": "",
            "hard": False,
            "task_type": "coding",
            "task_summary": "Add widget",
        },
    )
    monkeypatch.setattr(
        "langbridge_code.workflow.run.run_coder_reviewer_loop",
        lambda *args, **kwargs: calls.append(args) or (True, "REVIEW_VERDICT: PASS"),
    )

    reply = run_workflow("key", "model", "add a widget", run_log, 1, print_reply=False)

    assert calls
    assert "Workflow complete" in reply
    assert "Add widget" in reply


def test_workflow_hard_task_invokes_planner(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    planner_calls = []

    monkeypatch.setattr(
        "langbridge_code.workflow.run.route",
        lambda *args, **kwargs: {
            "kind": "task",
            "reply": "",
            "hard": True,
            "task_type": "coding",
            "task_summary": "Build auth system",
        },
    )

    def fake_planner(*args, **kwargs):
        planner_calls.append(args)
        _write_todo(run_log, ["- [ ] [coding] Build auth system"])

    monkeypatch.setattr("langbridge_code.workflow.run.run_planner", fake_planner)
    monkeypatch.setattr(
        "langbridge_code.workflow.run.run_coder_reviewer_loop",
        lambda *args, **kwargs: (True, "done"),
    )

    reply = run_workflow("key", "model", "build auth", run_log, 1, print_reply=False)

    assert planner_calls
    assert "Workflow complete" in reply


def test_workflow_refines_plan_on_coder_failure(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    _write_todo(run_log, ["- [ ] [coding] Fix login"])

    monkeypatch.setattr(
        "langbridge_code.workflow.run.route",
        lambda *args, **kwargs: {
            "kind": "task",
            "reply": "",
            "hard": True,
            "task_type": "coding",
            "task_summary": "Fix login",
        },
    )
    monkeypatch.setattr("langbridge_code.workflow.run.run_planner", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "langbridge_code.workflow.run.run_coder_reviewer_loop",
        lambda *args, **kwargs: (False, "REVIEW_VERDICT: FAIL"),
    )

    refine_calls = []

    def fake_refine(*args, **kwargs):
        refine_calls.append(args[2] if len(args) > 2 else kwargs)

    monkeypatch.setattr("langbridge_code.workflow.run.run_planner", fake_refine)

    reply = run_workflow("key", "model", "fix login", run_log, 1, print_reply=False)

    assert refine_calls
    assert "Still open" in reply or "could not complete" in reply.lower()
