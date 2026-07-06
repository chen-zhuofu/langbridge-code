"""Git checkpoints for L4/L5 component tasks.

Each task attempt snapshots HEAD first. A passing L3 review is committed; a failed
attempt resets the workspace to the snapshot so half-finished code does not
accumulate. If the cwd is not a git repo, every call is a no-op.
"""
import subprocess
from pathlib import Path

from langbridge_cli.settings import WORKSPACE_ROOT


def _run_git(*args, cwd=None):
    cwd = cwd or WORKSPACE_ROOT
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _is_repo(cwd=None):
    cwd = cwd or WORKSPACE_ROOT
    return (Path(cwd) / ".git").exists()


def snapshot_head(cwd=None):
    """Return the current HEAD commit, or None when git is unavailable."""
    if not _is_repo(cwd):
        return None
    result = _run_git("rev-parse", "HEAD", cwd=cwd)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def commit_task(label, task, cwd=None):
    """Stage and commit workspace changes for a finished L4/L5 task."""
    if not _is_repo(cwd):
        return None
    _run_git("add", "-A", cwd=cwd)
    status = _run_git("diff", "--cached", "--quiet", cwd=cwd)
    if status.returncode == 0:
        return snapshot_head(cwd)
    message = f"{label}: {task[:72]}"
    result = _run_git("commit", "-m", message, cwd=cwd)
    if result.returncode != 0:
        return None
    return snapshot_head(cwd)


def commit_sub_task(sub_task, cwd=None):
    return commit_task("L5", sub_task, cwd=cwd)


def revert_snapshot(commit, cwd=None):
    """Drop tracked and untracked changes since `commit`, keeping agent-state."""
    if not commit or not _is_repo(cwd):
        return False
    reset = _run_git("reset", "--hard", commit, cwd=cwd)
    if reset.returncode != 0:
        return False
    clean = _run_git(
        "clean",
        "-fd",
        "-e",
        "agent-state",
        "-e",
        ".langbridge",
        cwd=cwd,
    )
    return clean.returncode == 0
