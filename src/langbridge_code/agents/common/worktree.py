"""Git worktree management for parallel worker execution (not an LLM tool)."""
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from langbridge_code.settings import AGENT_STATE_DIR, WORKSPACE_ROOT


@dataclass
class WorktreeInfo:
    branch: str
    path: Path
    task_description: str


def _run_git(*args, cwd=None):
    return subprocess.run(
        ["git", *args],
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


def branch_name(run_log_path, index: int, description: str) -> str:
    from langbridge_code.util.artifacts import artifact_dir

    directory = artifact_dir(run_log_path)
    stem = ((directory.name if directory else "session") or "session")[:24]
    return f"lb/{stem}/t{index}-{slugify(description)}"


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
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def record_branch(run_log_path, info: WorktreeInfo, status: str) -> None:
    data = load_registry(run_log_path)
    entry = {
        "branch": info.branch,
        "path": str(info.path),
        "task": info.task_description,
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
    data = load_registry(run_log_path)
    updated = False
    for item in data.get("branches", []):
        if item.get("branch") == branch:
            item["status"] = status
            updated = True
    if updated:
        save_registry(run_log_path, data)


def create_worktree(run_log_path, index: int, description: str) -> WorktreeInfo:
    branch = branch_name(run_log_path, index, description)
    base = worktrees_dir(run_log_path)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"t{index}-{slugify(description)}"
    if path.exists():
        remove_worktree(WorktreeInfo(branch=branch, path=path, task_description=description), force=True)
    result = _run_git("worktree", "add", "-b", branch, str(path), "HEAD")
    if result.returncode != 0:
        _run_git("branch", "-D", branch)
        result = _run_git("worktree", "add", "-b", branch, str(path), "HEAD")
    if result.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed for {branch}: {(result.stderr or result.stdout).strip()}"
        )
    return WorktreeInfo(branch=branch, path=path, task_description=description)


def remove_worktree(info: WorktreeInfo, *, force: bool = False) -> None:
    if not info.path.exists():
        return
    args = ["worktree", "remove", str(info.path)]
    if force:
        args.insert(1, "--force")
    _run_git(*args)
    if force:
        _run_git("branch", "-D", info.branch)
