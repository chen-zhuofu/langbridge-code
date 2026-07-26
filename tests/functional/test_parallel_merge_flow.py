"""End-to-end parallel flow with real git: worktree workers -> ready branches -> merge_branch."""
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.common.workspace import get_workspace_root
from langbridge_code.agents.worker_reviewer import build_agent_worker_tool
from langbridge_code.tools.merge_branch import merge_branch


def _git(cwd, *args, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check:
        assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"
    return result


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@test")
    _git(root, "config", "user.name", "test")
    (root / "base.txt").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "base")

    import langbridge_code.settings as settings
    import langbridge_code.agents.common.todo_list as todo_list_mod

    monkeypatch.setattr(settings, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(worktree_mod, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(worktree_mod, "AGENT_STATE_DIR", tmp_path / "agent-state")
    monkeypatch.setattr(
        "langbridge_code.tools.merge_branch.get_workspace_root", lambda: root
    )
    # The plan file lives at the (test) workspace root, not the repo running pytest.
    monkeypatch.setattr(todo_list_mod, "plan_path", lambda: root / "todo_list.md")
    return root


def test_parallel_workers_then_main_agent_merges(repo, tmp_path, monkeypatch):
    """Two parallel workers commit in worktrees; the main agent merges both branches."""
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.emit_phase", lambda *a, **k: None
    )

    run_log = tmp_path / "run.json"
    plan_file = repo / "todo_list.md"
    plan_file.write_text(
        "# Todo\n\n"
        "## Todo list\n"
        "- [ ] Add auth\n"
        "- [ ] Add billing\n"
        "- [ ] Verify merged codebase (after tasks 1 and 2)\n",
        encoding="utf-8",
    )
    _git(repo, "add", "todo_list.md")
    _git(repo, "commit", "-m", "plan")

    # Both workers must be in flight before either finishes, mirroring the main
    # agent dispatching two agent_worker calls in one turn (run_tool_calls threads).
    both_started = threading.Barrier(2, timeout=10)

    def fake_worker_loop(api_key, model, task, context, **kwargs):
        if task.lower().startswith("verify"):
            # Integration verification also runs in its own worktree now.
            assert kwargs["cwd"] == get_workspace_root()
            return True, "REVIEW_VERDICT: PASS"
        # Simulate a worker that implements its task inside the worktree.
        workdir = kwargs["cwd"]
        assert workdir == get_workspace_root(), "worker must run inside its worktree scope"
        both_started.wait()
        name = "auth" if "auth" in task.lower() else "billing"
        (workdir / f"{name}.py").write_text(f"feature = {name!r}\n")
        _git(workdir, "add", "-A")
        _git(workdir, "commit", "-m", f"add {name}")
        return True, "REVIEW_VERDICT: PASS"

    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        fake_worker_loop,
    )

    agent_worker = build_agent_worker_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
        messages=[],
        target="ship",
    )

    # Every coding dispatch gets its own worktree; here two run concurrently.
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_auth = pool.submit(
            agent_worker,
            prompt="Add auth",
            description="auth",
            task_name="task-auth",
        )
        future_billing = pool.submit(
            agent_worker,
            prompt="Add billing",
            description="billing",
            task_name="task-billing",
        )
        reply_auth = future_auth.result(timeout=30)
        reply_billing = future_billing.result(timeout=30)
    assert "Worktree task completed" in reply_auth
    assert "ready to merge" in reply_auth
    assert "Worktree task completed" in reply_billing
    # No auto-check: each reply tells the main agent to mark the todo itself.
    assert "mark that line `[x]` yourself" in reply_auth
    assert "mark that line `[x]` yourself" in reply_billing
    assert "- [ ] Add auth" in plan_file.read_text(encoding="utf-8")

    ready = worktree_mod.ready_branches(run_log)
    assert len(ready) == 2

    # Delegating the merge to a worker is rejected.
    rejected = agent_worker(
        prompt=f"Merge branch {ready[0]} into the main workspace", description="merge"
    )
    assert rejected.startswith("Tool error:")
    assert "merge_branch" in rejected

    # Main agent merges branch 1: file lands in the main workspace, worktree cleaned.
    branch_one = ready[0]
    reply = merge_branch(branch_one, run_log_path=run_log)
    assert f"Merged {branch_one!r}" in reply
    assert ready[1] in reply  # remaining branch listed
    merged_file = "auth.py" if "auth" in branch_one else "billing.py"
    assert (repo / merged_file).exists()
    assert worktree_mod.ready_branches(run_log) == [ready[1]]
    # Worktree directory and branch are gone.
    assert not any(
        entry["branch"] == branch_one and entry["status"] != "merged"
        for entry in worktree_mod.load_registry(run_log)["branches"]
    )
    assert branch_one not in _git(repo, "branch", "--list", branch_one).stdout

    # Merge branch 2: everything present, no ready branches left.
    branch_two = ready[1]
    reply = merge_branch(branch_two, run_log_path=run_log)
    assert f"Merged {branch_two!r}" in reply
    assert "No ready branches left" in reply
    assert (repo / "auth.py").exists()
    assert (repo / "billing.py").exists()
    assert worktree_mod.ready_branches(run_log) == []

    # The main agent marks finished todos [x] itself (no auto-check).
    plan = plan_file.read_text(encoding="utf-8")
    plan = plan.replace("- [ ] Add auth", "- [x] Add auth")
    plan = plan.replace("- [ ] Add billing", "- [x] Add billing")
    plan_file.write_text(plan, encoding="utf-8")

    # The integration verification todo still goes through agent_worker —
    # it mentions "merged" but must not be mistaken for a merge task.
    reply_verify = agent_worker(
        prompt="Verify merged codebase (after tasks 1 and 2)",
        description="verify",
        task_name="task-verify-merged",
    )
    assert "Worktree task completed" in reply_verify
    assert "mark that line `[x]` yourself" in reply_verify


def test_conflict_resolution_round_trip_with_real_worktree(repo, tmp_path):
    """Conflicting branch: merge_branch reports files, manual resolve, confirm cleans up."""
    run_log = tmp_path / "run.json"
    info = worktree_mod.create_worktree(
        run_log,
        "Edit base file",
        task_name="edit-base-file",
    )
    (info.path / "base.txt").write_text("worktree version\n")
    _git(info.path, "add", "-A")
    _git(info.path, "commit", "-m", "worktree change")
    worktree_mod.record_branch(run_log, info, "ready")

    (repo / "base.txt").write_text("main version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main change")

    reply = merge_branch(info.branch, run_log_path=run_log)
    assert "hit conflicts" in reply
    assert "base.txt" in reply
    # Merge left in progress in the main workspace.
    assert (repo / ".git" / "MERGE_HEAD").exists()

    # Calling again before resolving is a clear error, not a second merge.
    again = merge_branch(info.branch, run_log_path=run_log)
    assert "merge is already in progress" in again

    # Resolve like the main agent would: edit, add, commit, confirm.
    (repo / "base.txt").write_text("resolved\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--no-edit")

    confirm = merge_branch(info.branch, run_log_path=run_log)
    assert "is merged into HEAD" in confirm
    assert worktree_mod.ready_branches(run_log) == []
    assert not info.path.exists()
    assert info.branch not in _git(repo, "branch", "--list", info.branch).stdout


def test_merge_branch_refuses_current_branch(repo, tmp_path):
    reply = merge_branch("main", run_log_path=tmp_path / "run.json")
    assert reply.startswith("Tool error:")
    assert "currently checked-out" in reply
