"""Turn-scoped checkpoints: workspace git snapshot + session artifact backup.

A checkpoint captures what a faithful rewind needs: the working tree
(tracked dirty + untracked, non-ignored files) as a dangling git commit under
a private ref, plus a copy of the context-affecting session artifacts
(traces, session memory / legacy progress, plan, task state, worktree
registry). Git object creation and restoration always go through a private,
throwaway index file (``GIT_INDEX_FILE``) so the user's real index and
HEAD/branch are never read or written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from langbridge_code.settings import WORKSPACE_ROOT
from langbridge_code.tools.common.runtime import managed_binary
from langbridge_code.util.artifacts import artifact_dir

CHECKPOINTS_DIRNAME = "checkpoints"
CHECKPOINTS_INDEX = "index.json"
CHECKPOINT_REF_PREFIX = "refs/langbridge/checkpoints"

# Session artifacts (relative to the session directory) copied verbatim into
# each checkpoint snapshot so a rewind restores context, not just code.
_SNAPSHOT_FILES = (
    "traces.md",
    "session_memory.md",
    "progress.md",
    "todo_list.md",
    "worktrees.json",
)
_SNAPSHOT_DIRS = ("tasks",)

_checkpoints_lock = threading.Lock()


@dataclass
class CheckpointResult:
    turn_id: int
    workspace_captured: bool
    error: str | None = None


@dataclass
class RestoreResult:
    ok: bool
    error: str = ""


def _run_git(*args, cwd=None, env=None):
    try:
        return subprocess.run(
            [managed_binary("git"), *args],
            cwd=cwd or WORKSPACE_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return subprocess.CompletedProcess(args, 1, "", str(error))


def _is_git_repo(root) -> bool:
    return (Path(root) / ".git").exists()


def _capture_workspace_tree(root: Path) -> tuple[str | None, str | None]:
    """Return ``(tree_sha, head_sha)`` for the current dirty + untracked worktree.

    Uses a private scratch index so the real ``.git/index`` is never read or
    written. Returns ``(None, None)`` when this is not a usable git repo.
    """
    if not _is_git_repo(root):
        return None, None
    head = _run_git("rev-parse", "--verify", "HEAD", cwd=root)
    head_sha = head.stdout.strip() if head.returncode == 0 else None
    with tempfile.TemporaryDirectory(prefix="langbridge-checkpoint-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        if head_sha:
            seed = _run_git("read-tree", head_sha, cwd=root, env=env)
            if seed.returncode != 0:
                return None, head_sha
        add = _run_git("add", "--all", cwd=root, env=env)
        if add.returncode != 0:
            return None, head_sha
        write = _run_git("write-tree", cwd=root, env=env)
        if write.returncode != 0:
            return None, head_sha
        return write.stdout.strip(), head_sha


def _commit_checkpoint_tree(
    root: Path, tree_sha: str, head_sha: str | None, message: str
) -> str | None:
    args = [
        "-c",
        "user.email=langbridge@localhost",
        "-c",
        "user.name=LangBridge",
        "commit-tree",
        tree_sha,
    ]
    if head_sha:
        args += ["-p", head_sha]
    args += ["-m", message]
    result = _run_git(*args, cwd=root)
    return result.stdout.strip() if result.returncode == 0 else None


def _update_ref(root: Path, ref: str, commit_sha: str) -> bool:
    return _run_git("update-ref", ref, commit_sha, cwd=root).returncode == 0


def _checkpoint_ref(directory: Path, turn: int) -> str:
    return f"{CHECKPOINT_REF_PREFIX}/{directory.name}/turn-{turn}"


def _snapshot_dir(directory: Path, turn: int) -> Path:
    return directory / CHECKPOINTS_DIRNAME / f"turn-{turn}"


def _write_artifact_snapshot(directory: Path, snapshot_dir: Path) -> None:
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for name in _SNAPSHOT_FILES:
        source = directory / name
        if source.is_file():
            shutil.copy2(source, snapshot_dir / name)
    for name in _SNAPSHOT_DIRS:
        source = directory / name
        if source.is_dir():
            shutil.copytree(source, snapshot_dir / name)


def _restore_artifact_snapshot(directory: Path, snapshot_dir: Path) -> None:
    if not snapshot_dir.is_dir():
        return
    for name in _SNAPSHOT_FILES:
        source = snapshot_dir / name
        target = directory / name
        if source.is_file():
            shutil.copy2(source, target)
        elif target.exists():
            target.unlink()
    for name in _SNAPSHOT_DIRS:
        source = snapshot_dir / name
        target = directory / name
        if target.exists():
            shutil.rmtree(target)
        if source.is_dir():
            shutil.copytree(source, target)


def _index_path(directory: Path) -> Path:
    return directory / CHECKPOINTS_DIRNAME / CHECKPOINTS_INDEX


def _load_index(directory: Path) -> dict:
    path = _index_path(directory)
    if not path.exists():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": []}
    if not isinstance(data, dict):
        return {"entries": []}
    data.setdefault("entries", [])
    return data


def _save_index(directory: Path, data: dict) -> None:
    path = _index_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _record_entry(directory: Path, entry: dict) -> None:
    data = _load_index(directory)
    entries = [item for item in data["entries"] if item.get("turn_id") != entry["turn_id"]]
    entries.append(entry)
    data["entries"] = entries
    _save_index(directory, data)


def checkpoint_entry(run_log_path, turn_id: int) -> dict | None:
    """Return the recorded checkpoint metadata for ``turn_id``, if any."""
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    turn = int(turn_id or 0)
    for item in _load_index(directory).get("entries", []):
        if item.get("turn_id") == turn:
            return item
    return None


def checkpoint_available(run_log_path, turn_id: int) -> bool:
    """True only when both the workspace commit and the artifact snapshot exist."""
    entry = checkpoint_entry(run_log_path, turn_id)
    if not entry or not entry.get("commit"):
        return False
    directory = artifact_dir(run_log_path)
    if directory is None:
        return False
    snapshot_dir = directory / CHECKPOINTS_DIRNAME / str(entry.get("snapshot") or "")
    return snapshot_dir.is_dir()


def create_checkpoint(run_log_path, turn_id: int) -> CheckpointResult:
    """Snapshot the workspace and session artifacts before a turn begins.

    Never raises: git or filesystem failures are reported in the result so a
    checkpoint failure cannot crash a turn, and ``checkpoint_available``
    reports the checkpoint as unusable rather than silently pretending it
    restored correctly.
    """
    directory = artifact_dir(run_log_path)
    if directory is None:
        return CheckpointResult(turn_id=int(turn_id or 0), workspace_captured=False, error="no session directory")
    turn = int(turn_id or 0)
    with _checkpoints_lock:
        try:
            root = Path(WORKSPACE_ROOT)
            tree_sha, head_sha = _capture_workspace_tree(root)
            commit_sha = None
            if tree_sha:
                commit_sha = _commit_checkpoint_tree(
                    root, tree_sha, head_sha, f"LangBridge checkpoint: {directory.name} turn {turn}"
                )
                if commit_sha and not _update_ref(root, _checkpoint_ref(directory, turn), commit_sha):
                    commit_sha = None
            snapshot_dir = _snapshot_dir(directory, turn)
            _write_artifact_snapshot(directory, snapshot_dir)
            _record_entry(
                directory,
                {
                    "turn_id": turn,
                    "commit": commit_sha,
                    "tree": tree_sha if commit_sha else None,
                    "head_at_checkpoint": head_sha,
                    "workspace_root": str(root),
                    "snapshot": snapshot_dir.name,
                },
            )
            return CheckpointResult(turn_id=turn, workspace_captured=bool(commit_sha))
        except OSError as error:
            return CheckpointResult(turn_id=turn, workspace_captured=False, error=str(error))


def _restore_worktree_to_tree(root: Path, tree_sha: str) -> bool:
    desired = _run_git("ls-tree", "-r", "--name-only", "-z", tree_sha, cwd=root)
    if desired.returncode != 0:
        return False
    desired_paths = {p for p in desired.stdout.split("\0") if p}
    current = _run_git("ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=root)
    if current.returncode != 0:
        return False
    current_paths = {p for p in current.stdout.split("\0") if p}
    # Ignored files are never in current_paths (--exclude-standard) or
    # desired_paths (the checkpoint was captured the same way), so they are
    # never touched by either the deletion pass or the checkout pass below.
    for path in sorted(current_paths - desired_paths, reverse=True):
        target = root / path
        try:
            if target.is_file() or target.is_symlink():
                target.unlink()
        except OSError:
            pass
    with tempfile.TemporaryDirectory(prefix="langbridge-restore-") as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        read = _run_git("read-tree", tree_sha, cwd=root, env=env)
        if read.returncode != 0:
            return False
        checkout = _run_git("checkout-index", "-a", "-f", cwd=root, env=env)
        if checkout.returncode != 0:
            return False
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        current_dir = Path(dirpath)
        if ".git" in current_dir.parts:
            continue
        if not dirnames and not filenames and current_dir != root:
            try:
                current_dir.rmdir()
            except OSError:
                pass
    return True


def restore_checkpoint(run_log_path, turn_id: int) -> RestoreResult:
    """Restore the workspace and session artifacts to immediately before ``turn_id``.

    Never moves HEAD, a branch ref, or writes the real index: the checkpoint
    tree is materialized onto the worktree via a scratch index, and a safety
    checkpoint of the state being discarded is recorded first so an
    accidental rewind stays recoverable.
    """
    directory = artifact_dir(run_log_path)
    if directory is None:
        return RestoreResult(False, "no session directory")
    entry = checkpoint_entry(run_log_path, turn_id)
    if entry is None or not entry.get("commit") or not entry.get("tree"):
        return RestoreResult(False, "no checkpoint recorded for this turn")
    root = Path(entry.get("workspace_root") or WORKSPACE_ROOT)
    if not _is_git_repo(root):
        return RestoreResult(False, "workspace is not a git repository")
    with _checkpoints_lock:
        try:
            safety_tree, safety_head = _capture_workspace_tree(root)
            if safety_tree:
                safety_commit = _commit_checkpoint_tree(
                    root,
                    safety_tree,
                    safety_head,
                    f"LangBridge pre-rewind snapshot: {directory.name} before turn {int(turn_id)}",
                )
                if safety_commit:
                    import time

                    _update_ref(
                        root,
                        f"{CHECKPOINT_REF_PREFIX}/{directory.name}/pre-rewind-{int(time.time() * 1000)}",
                        safety_commit,
                    )
            if not _restore_worktree_to_tree(root, entry["tree"]):
                return RestoreResult(False, "failed to restore workspace files")
            snapshot_dir = directory / CHECKPOINTS_DIRNAME / str(entry.get("snapshot") or "")
            _restore_artifact_snapshot(directory, snapshot_dir)
            return RestoreResult(True)
        except OSError as error:
            return RestoreResult(False, str(error))
