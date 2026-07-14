from langbridge_code.agents.common.todo_list import TodoTask, write_todo_list
from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.tools.agent_worker_reviewer import (
    build_agent_worker_tool,
    integration_pending_message,
    is_integration_task,
    is_merge_task_prompt,
    pending_integration_tasks,
)


def test_is_integration_task():
    assert is_integration_task(
        TodoTask("Verify merged codebase and run integration tests <!-- integration -->")
    )
    assert not is_integration_task(TodoTask("Build auth API"))


def test_integration_pending_message_lists_main_agent_steps(tmp_path):
    run_log = tmp_path / "run.json"
    tasks = [
        TodoTask("Build auth", done=True),
        TodoTask("Verify merged codebase <!-- integration -->"),
    ]
    worktree_mod.record_branch(
        run_log,
        worktree_mod.WorktreeInfo("lb/session/t1-auth", tmp_path / "wt", "Build auth"),
        "ready",
    )
    message = integration_pending_message(tasks, ["Build auth"], run_log_path=run_log)
    assert "Main agent next steps" in message
    assert "merge_branch" in message
    assert "agent_worker" in message
    assert "lb/session/t1-auth" in message
    assert "Verify merged codebase" in message


def test_dispatch_worker_auto_marks_todo_on_pass(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    content = """# Todo

## Todo list
- [ ] Create HTML slides
- [ ] Browser verify
"""
    from langbridge_code.agents.common import todo_list as common

    common.write_todo_list(content, run_log_path=run_log)
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (True, "REVIEW_VERDICT: PASS"),
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.emit_phase",
        lambda *args, **kwargs: None,
    )

    agent_worker = build_agent_worker_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
        messages=[],
        target="Create HTML slides",
    )
    reply = agent_worker(
        prompt="Create HTML slides",
        description="worker",
    )

    assert "Single-task completed" in reply
    assert "Marked complete: Create HTML slides" in reply
    updated = common.read_todo_list(run_log)
    assert "- [x] Create HTML slides" in updated
    assert "- [ ] Browser verify" in updated


def test_dispatch_worker_does_not_auto_refine_plan(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    planner_calls = []

    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (False, "REVIEW_VERDICT: FAIL"),
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_planner.run_planner",
        lambda *args, **kwargs: planner_calls.append(True),
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.emit_phase",
        lambda *args, **kwargs: None,
    )

    agent_worker = build_agent_worker_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
        messages=[],
        target="fix login",
    )
    reply = agent_worker(prompt="Fix login", description="worker")

    assert not planner_calls
    assert "stopped (review did not pass)" in reply


def test_dispatch_ready_wave_worker_uses_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.settings.PARALLEL_AGENTS_ENABLED", True)
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.PARALLEL_AGENTS_ENABLED",
        True,
    )
    run_log = tmp_path / "run.json"
    write_todo_list(
        "# Todo\n\n"
        "- [ ] Add auth <!-- depends: none -->\n"
        "- [ ] Add billing <!-- depends: none -->\n"
        "- [ ] Wire <!-- depends: 1, 2 -->\n",
        run_log_path=run_log,
    )
    info = worktree_mod.WorktreeInfo("lb/run/t1-auth", tmp_path / "wt", "Add auth")
    captured = {}

    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.worktree_mod.is_git_repo",
        lambda cwd=None: True,
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.worktree_mod.create_worktree",
        lambda *args, **kwargs: info,
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.worktree_mod.record_branch",
        lambda run_log_path, wt_info, status: captured.update({"status": status}),
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (True, "REVIEW_VERDICT: PASS"),
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.emit_phase",
        lambda *args, **kwargs: None,
    )

    agent_worker = build_agent_worker_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
        messages=[],
        target="ship",
    )
    reply = agent_worker(
        prompt="Add auth <!-- depends: none -->",
        description="auth",
    )

    assert captured["status"] == "ready"
    assert "Parallel worktree completed" in reply
    assert "lb/run/t1-auth" in reply


def test_is_merge_task_prompt():
    assert is_merge_task_prompt("Merge branch lb/session/t1-auth into main workspace")
    assert is_merge_task_prompt("Run git merge lb/foo/bar")
    assert not is_merge_task_prompt("Add auth <!-- depends: none -->")


def test_agent_worker_rejects_merge_prompts(tmp_path, monkeypatch):
    """Merges are the main agent's job (merge_branch tool), never a worker's."""
    run_log = tmp_path / "run.json"
    worktree_mod.record_branch(
        run_log,
        worktree_mod.WorktreeInfo("lb/run/t1-auth", tmp_path / "wt", "Add auth"),
        "ready",
    )
    loop_calls = []
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: loop_calls.append(True) or (True, "REVIEW_VERDICT: PASS"),
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.emit_phase",
        lambda *args, **kwargs: None,
    )

    agent_worker = build_agent_worker_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
        messages=[],
        target="ship",
    )
    reply = agent_worker(
        prompt="Merge branch lb/run/t1-auth into the main workspace",
        description="merge",
    )

    assert reply.startswith("Tool error:")
    assert "merge_branch" in reply
    assert not loop_calls
    # The ready branch stays queued for the main agent to merge itself.
    assert worktree_mod.ready_branches(run_log) == ["lb/run/t1-auth"]
