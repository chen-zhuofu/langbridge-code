"""merge_branch: main agent merges ready feature branches itself (no worker)."""
import subprocess
from pathlib import Path

from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.common.workspace import get_workspace_root
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER

MERGE_BRANCH_TOOL_SCHEMA = {
    "type": "function",
    "name": "merge_branch",
    "description": (
        "Merge one ready feature branch (from parallel agent_worker runs) into the "
        "main workspace. Main agent only — do not dispatch merge tasks to agent_worker. "
        "Call once per branch listed by read_plan. On success the branch is marked "
        "merged and its worktree is cleaned up. On conflicts the merge is left in "
        "progress: resolve the listed files yourself with edit_file, stage with bash "
        "`git add`, commit with git_commit (or bash `git commit --no-edit`), then call "
        "merge_branch again with the same branch to confirm and clean up."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": PURPOSE_PARAMETER,
            "branch": {
                "type": "string",
                "description": "Feature branch to merge (e.g. lb/session/t1-auth).",
            },
        },
        "required": ["purpose", "branch"],
        "additionalProperties": False,
    },
}

TOOL_SCHEMAS = [MERGE_BRANCH_TOOL_SCHEMA]

TOOLS = {}


def _run_git(*args, cwd=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd or get_workspace_root(),
        capture_output=True,
        text=True,
    )


def _registry_entry(run_log_path, branch: str) -> dict | None:
    for item in worktree_mod.load_registry(run_log_path).get("branches", []):
        if item.get("branch") == branch:
            return item
    return None


def _cleanup_branch(run_log_path, branch: str) -> None:
    worktree_mod.mark_branch_status(run_log_path, branch, "merged")
    entry = _registry_entry(run_log_path, branch)
    path = Path(entry["path"]) if entry and entry.get("path") else None
    if path is not None and path.exists():
        worktree_mod.remove_worktree(
            worktree_mod.WorktreeInfo(branch=branch, path=path, task_description=""),
            force=True,
        )
    else:
        _run_git("branch", "-D", branch)


def _conflicted_files() -> list[str]:
    result = _run_git("diff", "--name-only", "--diff-filter=U")
    return [line for line in (result.stdout or "").splitlines() if line.strip()]


def _merge_in_progress() -> bool:
    result = _run_git("rev-parse", "--git-path", "MERGE_HEAD")
    merge_head = (result.stdout or "").strip()
    if not merge_head:
        return False
    return (get_workspace_root() / merge_head).exists()


def _branch_is_merged(branch: str) -> bool:
    return _run_git("merge-base", "--is-ancestor", branch, "HEAD").returncode == 0


def _remaining_note(run_log_path) -> str:
    remaining = worktree_mod.ready_branches(run_log_path)
    if remaining:
        return "Remaining ready branches:\n" + "\n".join(f"- {b}" for b in remaining)
    return "No ready branches left. Dispatch dependent / integration todos next."


def merge_branch(branch, run_log_path=None):
    branch = (branch or "").strip()
    if not branch:
        return "Tool error: branch must be a non-empty string."
    if not worktree_mod.is_git_repo(get_workspace_root()):
        return "Tool error: the workspace is not a git repository."

    ready = worktree_mod.ready_branches(run_log_path)

    # Confirmation call after manual conflict resolution.
    if _branch_is_merged(branch):
        if _merge_in_progress():
            return (
                f"Merge of {branch!r} is still in progress (unfinished commit). "
                "Stage resolved files with `git add`, then `git commit --no-edit`, "
                "then call merge_branch again."
            )
        _cleanup_branch(run_log_path, branch)
        return (
            f"Branch {branch!r} is merged into HEAD. Marked merged and cleaned up "
            f"its worktree.\n\n{_remaining_note(run_log_path)}"
        )

    if ready and branch not in ready:
        return (
            f"Tool error: branch {branch!r} is not in ready branches. "
            "read_plan and merge one ready branch per call.\n"
            "Ready branches:\n" + "\n".join(f"- {b}" for b in ready)
        )

    if _merge_in_progress():
        conflicts = _conflicted_files()
        listing = "\n".join(f"- {f}" for f in conflicts) or "- (run git status to inspect)"
        return (
            "Tool error: a merge is already in progress. Finish it first — resolve:\n"
            f"{listing}\n"
            "then `git add` the files and `git commit --no-edit`."
        )

    result = _run_git("merge", "--no-edit", branch)
    if result.returncode == 0:
        _cleanup_branch(run_log_path, branch)
        summary = (result.stdout or "").strip()
        return (
            f"Merged {branch!r} into the main workspace.\n"
            f"{summary}\n\n{_remaining_note(run_log_path)}"
        )

    conflicts = _conflicted_files()
    if conflicts:
        listing = "\n".join(f"- {f}" for f in conflicts)
        return (
            f"Merge of {branch!r} hit conflicts. The merge is left in progress.\n"
            f"Conflicted files:\n{listing}\n\n"
            "Resolve each file with edit_file (remove conflict markers), stage with "
            "bash `git add <files>`, commit with `git commit --no-edit`, then call "
            "merge_branch again with the same branch to confirm and clean up."
        )
    error_text = (result.stderr or result.stdout or "").strip()
    return f"Tool error: git merge failed for {branch!r}: {error_text}"


TOOLS["merge_branch"] = merge_branch
