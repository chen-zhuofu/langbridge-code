import subprocess

import pytest

from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.tools.merge_branch import merge_branch


def _git(repo, *args):
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


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
    monkeypatch.setattr(
        "langbridge_code.tools.merge_branch.get_workspace_root", lambda: root
    )
    return root


def _make_branch(repo, name, filename, content):
    _git(repo, "checkout", "-b", name)
    (repo / filename).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"work on {name}")
    _git(repo, "checkout", "main")


def test_merge_branch_success_marks_merged(repo, tmp_path):
    run_log = tmp_path / "run.json"
    branch = "lb/run/t1-auth"
    _make_branch(repo, branch, "auth.txt", "auth\n")
    worktree_mod.record_branch(
        run_log,
        worktree_mod.WorktreeInfo(branch, tmp_path / "missing-wt", "Add auth"),
        "ready",
    )

    reply = merge_branch(branch, run_log_path=run_log)

    assert f"Merged {branch!r}" in reply
    assert "No ready branches left" in reply
    assert (repo / "auth.txt").exists()
    assert worktree_mod.ready_branches(run_log) == []


def test_merge_branch_rejects_unknown_branch(repo, tmp_path):
    run_log = tmp_path / "run.json"
    _make_branch(repo, "lb/run/t1-auth", "auth.txt", "auth\n")
    worktree_mod.record_branch(
        run_log,
        worktree_mod.WorktreeInfo("lb/run/t1-auth", tmp_path / "wt", "Add auth"),
        "ready",
    )

    reply = merge_branch("lb/run/t9-nope", run_log_path=run_log)

    assert reply.startswith("Tool error:")
    assert "not in ready branches" in reply
    assert worktree_mod.ready_branches(run_log) == ["lb/run/t1-auth"]


def test_merge_branch_conflict_leaves_merge_in_progress(repo, tmp_path):
    run_log = tmp_path / "run.json"
    branch = "lb/run/t1-auth"
    _make_branch(repo, branch, "base.txt", "branch version\n")
    (repo / "base.txt").write_text("main version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main change")
    worktree_mod.record_branch(
        run_log,
        worktree_mod.WorktreeInfo(branch, tmp_path / "wt", "Add auth"),
        "ready",
    )

    reply = merge_branch(branch, run_log_path=run_log)

    assert "hit conflicts" in reply
    assert "base.txt" in reply
    assert worktree_mod.ready_branches(run_log) == [branch]

    # Resolve the conflict the way the main agent would, then confirm.
    (repo / "base.txt").write_text("resolved\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--no-edit")

    confirm = merge_branch(branch, run_log_path=run_log)
    assert "is merged into HEAD" in confirm
    assert worktree_mod.ready_branches(run_log) == []
