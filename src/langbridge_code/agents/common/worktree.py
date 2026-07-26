"""Git worktree management for parallel worker execution (not an LLM tool)."""
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from langbridge_code.settings import AGENT_STATE_DIR, WORKSPACE_ROOT
from langbridge_code.tools.common.runtime import managed_binary

# Parallel workers update the registry concurrently; serialize the
# read-modify-write so entries are not lost.
_REGISTRY_LOCK = threading.Lock()


@dataclass
class WorktreeInfo:
    branch: str
    path: Path
    task_description: str
    task_name: str = ""
    base_commit: str | None = None


def _run_git(*args, cwd=None):
    return subprocess.run(
        [managed_binary("git"), *args],
        cwd=cwd or WORKSPACE_ROOT,
        capture_output=True,
        text=True,
    )


def is_git_repo(cwd=None) -> bool:
    root = Path(cwd or WORKSPACE_ROOT)
    return (root / ".git").exists()


def slugify(text: str, max_len: int = 28) -> str:
    cleaned = re.sub(r"<!--.*?-->", "", text or "", flags=re.IGNORECASE)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", cleaned.lower()).strip("-")
    if not slug:
        slug = "task"
    return slug[:max_len].strip("-") or "task"


def branch_name(run_log_path, task_name: str) -> str:
    from langbridge_code.util.artifacts import artifact_dir

    directory = artifact_dir(run_log_path)
    stem = ((directory.name if directory else "session") or "session")[:24]
    return f"lb/{stem}/{slugify(task_name, max_len=48)}"


def worktrees_dir(run_log_path) -> Path:
    from langbridge_code.util.artifacts import artifact_dir

    directory = artifact_dir(run_log_path)
    stem = (directory.name if directory else "default") or "default"
    return AGENT_STATE_DIR / "workflow" / "worktrees" / stem


def registry_path(run_log_path):
    from langbridge_code.util.artifacts import artifact_dir

    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / "worktrees.json"


def load_registry(run_log_path) -> dict:
    path = registry_path(run_log_path)
    if path is None or not path.exists():
        return {"branches": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"branches": []}
    if not isinstance(data, dict):
        return {"branches": []}
    data.setdefault("branches", [])
    return data


def save_registry(run_log_path, data: dict) -> None:
    path = registry_path(run_log_path)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace so concurrent readers never see a half-written file.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record_branch(run_log_path, info: WorktreeInfo, status: str) -> None:
    with _REGISTRY_LOCK:
        data = load_registry(run_log_path)
        entry = {
            "branch": info.branch,
            "path": str(info.path),
            "task": info.task_description,
            "task_name": info.task_name,
            "base_commit": info.base_commit,
            "status": status,
        }
        branches = [item for item in data["branches"] if item.get("branch") != info.branch]
        branches.append(entry)
        data["branches"] = branches
        save_registry(run_log_path, data)


def ready_branches(run_log_path) -> list[str]:
    return [
        item["branch"]
        for item in load_registry(run_log_path).get("branches", [])
        if item.get("status") == "ready" and item.get("branch")
    ]


def mark_branch_status(run_log_path, branch: str, status: str) -> None:
    with _REGISTRY_LOCK:
        data = load_registry(run_log_path)
        updated = False
        for item in data.get("branches", []):
            if item.get("branch") == branch:
                item["status"] = status
                updated = True
        if updated:
            save_registry(run_log_path, data)


def create_worktree(
    run_log_path,
    description: str,
    *,
    task_name: str,
) -> WorktreeInfo:
    stable_name = (task_name or "").strip()
    if not stable_name:
        raise RuntimeError("task_name is required to create a stable worktree.")
    task_slug = slugify(stable_name, max_len=48)
    branch = branch_name(run_log_path, stable_name)
    base = worktrees_dir(run_log_path)
    base.mkdir(parents=True, exist_ok=True)
    path = base / task_slug
    base_result = _run_git("rev-parse", "HEAD")
    base_commit = base_result.stdout.strip() if base_result.returncode == 0 else None
    if path.exists():
        raise RuntimeError(
            f"Stable worktree path already exists for {stable_name!r}: {path}. "
            "Resume it through the session registry instead of replacing it."
        )
    result = _run_git("worktree", "add", "-b", branch, str(path), "HEAD")
    if result.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed for {branch}: {(result.stderr or result.stdout).strip()}"
        )
    return WorktreeInfo(
        branch=branch,
        path=path,
        task_description=description,
        task_name=stable_name,
        base_commit=base_commit,
    )


def resumable_worktree(
    run_log_path,
    *,
    task_name: str,
    task_description: str,
) -> WorktreeInfo | None:
    """Return/recreate the failed worktree for the exact same task."""
    stable_name = (task_name or "").strip()
    if not stable_name:
        return None
    entries = list(reversed(load_registry(run_log_path).get("branches", [])))
    for entry in entries:
        if entry.get("status") != "failed":
            continue
        recorded_name = str(entry.get("task_name") or "").strip()
        if recorded_name != stable_name:
            continue
        if entry.get("task") != task_description:
            continue
        branch = str(entry.get("branch") or "")
        path_text = str(entry.get("path") or "")
        if not branch or not path_text:
            continue
        path = Path(path_text)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            result = _run_git("worktree", "add", str(path), branch)
            if result.returncode != 0:
                continue
        base_commit = entry.get("base_commit")
        if not base_commit:
            result = _run_git("merge-base", branch, "HEAD")
            if result.returncode == 0:
                base_commit = result.stdout.strip()
        return WorktreeInfo(
            branch=branch,
            path=path,
            task_description=task_description,
            task_name=stable_name,
            base_commit=base_commit or None,
        )
    return None


def remove_worktree(info: WorktreeInfo, *, force: bool = False) -> bool:
    if not info.path.exists():
        return True
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(info.path))
    result = _run_git(*args)
    if result.returncode != 0:
        return False
    if force:
        _run_git("branch", "-D", info.branch)
    return True
