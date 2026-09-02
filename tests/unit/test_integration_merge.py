import pytest

from langbridge_code.agents.common import control
from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.worker_reviewer import (
    build_agent_worker_tool,
    is_merge_task_prompt,
)


def test_dispatch_worker_pass_instructs_main_agent_to_mark_todo(tmp_path, monkeypatch):
    """No auto-check: the plan file is untouched and the reply tells the main
    agent to mark the todo [x] itself before dispatching the next wave."""
    run_log = tmp_path / "run.json"
    plan = "# Todo\n\n## Todo list\n- [ ] Create HTML slides\n- [ ] Browser verify\n"
    (tmp_path / "todo_list.md").write_text(plan, encoding="utf-8")
    info = worktree_mod.WorktreeInfo(
        "lb/run/t1-slides",
        tmp_path / "wt",
        "Create HTML slides",
        task_name="task-slides",
    )
    monkeypatch.setattr(
        "langbridge_code.agents.common.todo_list.plan_path",
        lambda: tmp_path / "todo_list.md",
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.ensure_git_repo",
        lambda cwd=None: tmp_path,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.resumable_worktree",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.create_worktree",
        lambda *args, **kwargs: info,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.record_branch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (True, "REVIEW_VERDICT: PASS"),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.emit_phase",
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
        task_name="task-slides",
    )

    assert "Worktree task completed" in reply
    assert "mark that line `[x]` yourself" in reply
    assert (tmp_path / "todo_list.md").read_text(encoding="utf-8") == plan


def test_dispatch_worker_does_not_auto_refine_plan(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    planner_calls = []
    info = worktree_mod.WorktreeInfo(
        "lb/run/t1-login",
        tmp_path / "wt",
        "Fix login",
        task_name="task-login",
    )

    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.ensure_git_repo",
        lambda cwd=None: tmp_path,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.resumable_worktree",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.create_worktree",
        lambda *args, **kwargs: info,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.record_branch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.commit_task",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (False, "REVIEW_VERDICT: FAIL"),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.planner.run_planner",
        lambda *args, **kwargs: planner_calls.append(True),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.emit_phase",
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
    reply = agent_worker(
        prompt="Fix login",
        description="worker",
        task_name="task-login",
    )

    assert not planner_calls
    assert "stopped before approval" in reply


def test_dispatch_worker_uses_worktree_by_default(tmp_path, monkeypatch):
    """A single sequential coding dispatch is isolated in a worktree too."""
    run_log = tmp_path / "run.json"
    info = worktree_mod.WorktreeInfo("lb/run/t1-auth", tmp_path / "wt", "Add auth")
    captured = {}

    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.ensure_git_repo",
        lambda cwd=None: tmp_path,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.resumable_worktree",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.create_worktree",
        lambda *args, **kwargs: info,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.record_branch",
        lambda run_log_path, wt_info, status: captured.update({"status": status}),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (True, "REVIEW_VERDICT: PASS"),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.emit_phase",
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
        prompt="Add auth",
        description="auth",
        task_name="task-auth",
    )

    assert captured["status"] == "ready"
    assert "Worktree task completed" in reply
    assert "lb/run/t1-auth" in reply


def test_dispatch_worker_failure_records_failed_branch_with_partial_work(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    info = worktree_mod.WorktreeInfo("lb/run/t1-auth", tmp_path / "wt", "Add auth")
    captured = {}

    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.ensure_git_repo",
        lambda cwd=None: tmp_path,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.resumable_worktree",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.create_worktree",
        lambda *args, **kwargs: info,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.record_branch",
        lambda run_log_path, wt_info, status: captured.update({"status": status}),
    )
    commits = []
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.commit_task",
        lambda label, task, cwd=None: commits.append((label, cwd)),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (False, "REVIEW_VERDICT: FAIL"),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.emit_phase",
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
        prompt="Add auth",
        description="auth",
        task_name="task-auth",
    )

    assert captured["status"] == "failed"
    assert commits == [("worker-partial", info.path)]
    assert "Worktree task stopped" in reply
    assert "partial work is committed on this branch" in reply


def test_dispatch_worker_hard_stop_records_resumable_worktree_without_commit(
    tmp_path, monkeypatch
):
    run_log = tmp_path / "run.json"
    info = worktree_mod.WorktreeInfo(
        "lb/run/t1-auth",
        tmp_path / "wt",
        "Add auth",
        task_name="task-auth",
    )
    captured = {}
    commits = []

    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.ensure_git_repo",
        lambda cwd=None: tmp_path,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.resumable_worktree",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.create_worktree",
        lambda *args, **kwargs: info,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.record_branch",
        lambda run_log_path, wt_info, status: captured.update({"status": status}),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.commit_task",
        lambda *args, **kwargs: commits.append(args),
    )

    def stopped(*args, **kwargs):
        raise control.StopRequested()

    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        stopped,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.emit_phase",
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
    with pytest.raises(control.StopRequested):
        agent_worker(
            prompt="Add auth",
            description="auth",
            task_name="task-auth",
        )

    assert captured["status"] == "failed"
    assert commits == []


def test_dispatch_worker_ensures_git_repo_before_worktree(tmp_path, monkeypatch):
    """Non-git workspaces are bootstrapped before worktree creation."""
    run_log = tmp_path / "run.json"
    info = worktree_mod.WorktreeInfo(
        "lb/run/t1-auth",
        tmp_path / "wt",
        "Add auth",
        task_name="task-auth",
    )
    calls = []
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.ensure_git_repo",
        lambda cwd=None: calls.append("ensure") or tmp_path,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.resumable_worktree",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.create_worktree",
        lambda *args, **kwargs: calls.append("create") or info,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.record_branch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (True, "REVIEW_VERDICT: PASS"),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.emit_phase",
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
        prompt="Add auth",
        description="auth",
        task_name="task-auth",
    )

    assert calls == ["ensure", "create"]
    assert "Worktree task completed" in reply


def test_is_merge_task_prompt():
    assert is_merge_task_prompt("Merge branch lb/session/t1-auth into main workspace")
    assert is_merge_task_prompt("Run git merge lb/foo/bar")
    assert not is_merge_task_prompt("Add auth")
    # Integration verification todos mention merged branches but are worker tasks.
    assert not is_merge_task_prompt("Verify merged branches work together")
    assert not is_merge_task_prompt("Verify merged codebase and run integration tests")


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
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: loop_calls.append(True) or (True, "REVIEW_VERDICT: PASS"),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.emit_phase",
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
