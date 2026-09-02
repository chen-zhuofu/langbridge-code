import subprocess

import pytest

from langbridge_code.util import checkpoints as ckpt


def _git(*args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    _git("init", cwd=root)
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git(
        "-c", "user.email=t@example.com", "-c", "user.name=Test",
        "commit", "-m", "seed", cwd=root,
    )
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", root)
    return root


@pytest.fixture()
def session(tmp_path):
    session_dir = tmp_path / "session-demo"
    session_dir.mkdir()
    (session_dir / "traces.md").write_text("# Session traces\n", encoding="utf-8")
    (session_dir / "session_memory.md").write_text(
        "# Session memory\noriginal note\n", encoding="utf-8"
    )
    return session_dir


def test_create_checkpoint_captures_workspace_and_marks_available(repo, session):
    result = ckpt.create_checkpoint(session, 1)

    assert result.workspace_captured is True
    assert result.error is None
    assert ckpt.checkpoint_available(session, 1) is True


def test_create_checkpoint_survives_process_restart_via_disk_index(repo, session):
    ckpt.create_checkpoint(session, 1)

    # Re-read as a brand-new process would: nothing but the on-disk index.
    assert ckpt.checkpoint_available(session, 1) is True
    entry = ckpt.checkpoint_entry(session, 1)
    assert entry is not None
    assert entry["commit"]


def test_restore_checkpoint_reverts_tracked_and_untracked_changes(repo, session):
    ckpt.create_checkpoint(session, 1)
    (repo / "tracked.txt").write_text("changed after checkpoint\n", encoding="utf-8")
    (repo / "new_untracked.txt").write_text("added later\n", encoding="utf-8")

    restore = ckpt.restore_checkpoint(session, 1)

    assert restore.ok is True
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "original\n"
    assert not (repo / "new_untracked.txt").exists()


def test_restore_checkpoint_recreates_files_deleted_after_checkpoint(repo, session):
    ckpt.create_checkpoint(session, 1)
    (repo / "tracked.txt").unlink()

    restore = ckpt.restore_checkpoint(session, 1)

    assert restore.ok is True
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "original\n"


def test_restore_checkpoint_never_touches_ignored_files(repo, session):
    (repo / "ignored.txt").write_text("build output\n", encoding="utf-8")
    ckpt.create_checkpoint(session, 1)
    (repo / "ignored.txt").write_text("changed ignored content\n", encoding="utf-8")

    ckpt.restore_checkpoint(session, 1)

    assert (repo / "ignored.txt").read_text(encoding="utf-8") == "changed ignored content\n"


def test_restore_checkpoint_reverts_plan_registry_and_task_state(repo, session):
    (session / "todo_list.md").write_text("- [ ] item one\n", encoding="utf-8")
    (session / "worktrees.json").write_text('{"branches": []}\n', encoding="utf-8")
    task_dir = session / "tasks" / "alpha"
    task_dir.mkdir(parents=True)
    (task_dir / "note.md").write_text("alpha state\n", encoding="utf-8")

    ckpt.create_checkpoint(session, 1)

    (session / "todo_list.md").write_text(
        "- [x] item one\n- [ ] item two\n", encoding="utf-8"
    )
    (session / "worktrees.json").write_text(
        '{"branches": [{"branch": "lb/x"}]}\n', encoding="utf-8"
    )
    (task_dir / "note.md").write_text("alpha state changed\n", encoding="utf-8")
    later_task_dir = session / "tasks" / "beta"
    later_task_dir.mkdir(parents=True)
    (later_task_dir / "note.md").write_text("beta state\n", encoding="utf-8")

    restore = ckpt.restore_checkpoint(session, 1)

    assert restore.ok is True
    assert (session / "todo_list.md").read_text(encoding="utf-8") == "- [ ] item one\n"
    assert (session / "worktrees.json").read_text(encoding="utf-8") == '{"branches": []}\n'
    assert (task_dir / "note.md").read_text(encoding="utf-8") == "alpha state\n"
    assert not later_task_dir.exists()


def test_restore_checkpoint_reverts_session_memory(repo, session):
    ckpt.create_checkpoint(session, 1)
    (session / "session_memory.md").write_text(
        "# Session memory\nlater note\n", encoding="utf-8"
    )

    ckpt.restore_checkpoint(session, 1)

    assert (session / "session_memory.md").read_text(encoding="utf-8") == (
        "# Session memory\noriginal note\n"
    )


def test_restore_checkpoint_reverts_traces_to_before_the_turn(repo, session):
    ckpt.create_checkpoint(session, 1)
    (session / "traces.md").write_text(
        "# Session traces\n\n## Turn 1\n\nlater content\n", encoding="utf-8"
    )

    ckpt.restore_checkpoint(session, 1)

    assert (session / "traces.md").read_text(encoding="utf-8") == "# Session traces\n"


def test_checkpoint_and_restore_never_move_head_branch_or_real_index(repo, session):
    head_before = _git("rev-parse", "HEAD", cwd=repo).strip()
    branch_before = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo).strip()
    index_path = repo / ".git" / "index"
    index_before = index_path.read_bytes()

    ckpt.create_checkpoint(session, 1)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    ckpt.restore_checkpoint(session, 1)

    assert _git("rev-parse", "HEAD", cwd=repo).strip() == head_before
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo).strip() == branch_before
    assert index_path.read_bytes() == index_before


def test_checkpoint_available_false_when_no_checkpoint_recorded(repo, session):
    assert ckpt.checkpoint_available(session, 7) is False


def test_restore_checkpoint_fails_cleanly_when_none_recorded(repo, session):
    result = ckpt.restore_checkpoint(session, 99)

    assert result.ok is False
    assert result.error


def test_create_checkpoint_does_not_raise_when_workspace_is_not_a_git_repo(tmp_path, session, monkeypatch):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", not_a_repo)

    result = ckpt.create_checkpoint(session, 1)

    assert result.workspace_captured is False
    assert ckpt.checkpoint_available(session, 1) is False
