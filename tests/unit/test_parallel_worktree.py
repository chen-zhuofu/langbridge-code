import subprocess
from unittest.mock import patch

from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.common.workspace import get_workspace_root, workspace_scope


def test_workspace_scope_switches_root(tmp_path, monkeypatch):
    import langbridge_code.settings as settings

    main = tmp_path / "main"
    other = tmp_path / "other"
    main.mkdir()
    other.mkdir()
    (main / "marker").write_text("main", encoding="utf-8")
    (other / "marker").write_text("other", encoding="utf-8")
    monkeypatch.setattr(settings, "WORKSPACE_ROOT", main)

    assert (get_workspace_root() / "marker").read_text(encoding="utf-8") == "main"
    with workspace_scope(other):
        assert (get_workspace_root() / "marker").read_text(encoding="utf-8") == "other"
    assert (get_workspace_root() / "marker").read_text(encoding="utf-8") == "main"


def test_worktree_registry_records_ready_branch(tmp_path):
    run_log = tmp_path / "run.json"
    info = worktree_mod.WorktreeInfo(
        branch="lb/session/t1-auth",
        path=tmp_path / "wt",
        task_description="Add auth",
    )
    worktree_mod.record_branch(run_log, info, "ready")
    assert worktree_mod.ready_branches(run_log) == ["lb/session/t1-auth"]


def test_failed_worktree_resumes_by_task_name_only(tmp_path):
    run_log = tmp_path / "run.json"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    info = worktree_mod.WorktreeInfo(
        branch="lb/session/t3-applications",
        path=worktree,
        task_description="Implement applications API",
        task_name="task-3-applications",
        base_commit="abc123",
    )
    worktree_mod.record_branch(run_log, info, "failed")

    resumed = worktree_mod.resumable_worktree(
        run_log,
        task_name="task-3-applications",
        task_description="Implement applications API",
    )
    assert resumed == info
    assert (
        worktree_mod.resumable_worktree(
            run_log,
            task_name="task-4-interviews",
            task_description="Implement applications API",
        )
        is None
    )
    # Same todo id resumes even when the contract string differs (real rewrites
    # must use a new id so they do not hit this path).
    resumed_new_contract = worktree_mod.resumable_worktree(
        run_log,
        task_name="task-3-applications",
        task_description="Changed contract wording",
    )
    assert resumed_new_contract is not None
    assert resumed_new_contract.task_name == "task-3-applications"
    assert resumed_new_contract.path == worktree
    assert resumed_new_contract.task_description == "Changed contract wording"


def test_reconcile_stale_working_marks_interrupted(tmp_path):
    run_log = tmp_path / "run.json"
    for name, status in (
        ("task-1", "merged"),
        ("task-2", "working"),
        ("task-3", "ready"),
    ):
        worktree_mod.record_branch(
            run_log,
            worktree_mod.WorktreeInfo(
                branch=f"lb/session/{name}",
                path=tmp_path / name,
                task_description=f"do {name}",
                task_name=name,
            ),
            status,
        )

    assert worktree_mod.reconcile_stale_working(run_log) == ["task-2"]
    statuses = {
        item["task_name"]: item["status"]
        for item in worktree_mod.registry_snapshot(run_log)
    }
    assert statuses == {"task-1": "merged", "task-2": "interrupted", "task-3": "ready"}
    # Idempotent: a second reconcile finds nothing stale.
    assert worktree_mod.reconcile_stale_working(run_log) == []


def test_interrupted_worktree_is_resumable(tmp_path):
    run_log = tmp_path / "run.json"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    info = worktree_mod.WorktreeInfo(
        branch="lb/session/task-2-levels",
        path=worktree,
        task_description="Build levels.js",
        task_name="task-2-levels",
        base_commit="abc123",
    )
    worktree_mod.record_branch(run_log, info, "working")
    worktree_mod.reconcile_stale_working(run_log)

    resumed = worktree_mod.resumable_worktree(
        run_log,
        task_name="task-2-levels",
        task_description="Build levels.js",
    )
    assert resumed == info


def test_build_subagent_state_empty_when_nothing_to_report():
    assert worktree_mod.build_subagent_state([], []) == ""


def test_build_subagent_state_lists_running_and_registry():
    pending = [
        {
            "call": {
                "name": "agent_worker",
                "call_id": "c1",
                "arguments": '{"task_name":"task-5-map"}',
            },
            "running_for_s": 125,
        }
    ]
    registry = [
        {"task_name": "task-3-sprites", "status": "ready"},
        {"task_name": "task-2-levels", "status": "interrupted"},
    ]
    text = worktree_mod.build_subagent_state(pending, registry)
    assert "RUNNING: agent_worker 'task-5-map' (elapsed 2m05s)" in text
    assert "task-3-sprites [ready]" in text
    assert "merge it with merge_branch" in text
    assert "task-2-levels [interrupted]" in text
    assert "re-dispatch" in text
    assert "new id in todo_list.md" in text


def test_build_subagent_state_says_nothing_running_when_runner_idle():
    registry = [{"task_name": "task-2-levels", "status": "interrupted"}]
    text = worktree_mod.build_subagent_state([], registry)
    assert "No subagent is running in this process right now." in text
    assert "Never infer that a task is still running" in text
    assert "Resume with the listed task_name" in text


def test_create_worktree_in_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    run_log = tmp_path / "run.json"
    with patch.object(worktree_mod, "WORKSPACE_ROOT", repo):
        with patch.object(worktree_mod, "AGENT_STATE_DIR", tmp_path / "agent-state"):
            info = worktree_mod.create_worktree(
                run_log,
                "Add auth API",
                task_name="task-auth-api",
            )
    assert info.path.exists()
    assert (info.path / "README").exists()
    assert info.path.name == "task-auth-api"
    assert info.branch.endswith("/task-auth-api")
    worktree_mod.remove_worktree(info, force=True)
