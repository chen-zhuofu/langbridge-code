"""Git inspection and commit helpers for agent tools."""
import json
import subprocess

from langbridge_code.agents.common.workspace import get_workspace_root
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER
from langbridge_code.tools.execution import resolve_workspace_path, truncate_output

DEFAULT_GIT_TIMEOUT_SECONDS = 120

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "git_status",
        "description": "Show git status for the current workspace (branch + changed files).",
        "parameters": {
            "type": "object",
            "properties": {"purpose": PURPOSE_PARAMETER},
            "required": ["purpose"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "git_diff",
        "description": "Show git diff for the workspace or one path.",
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "path": {
                    "type": "string",
                    "description": "Optional file or directory path relative to the workspace.",
                },
                "staged": {
                    "type": "boolean",
                    "description": "If true, show staged diff (--cached).",
                    "default": False,
                },
            },
            "required": ["purpose"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "git_commit",
        "description": (
            "Stage paths (or all changes when paths omitted) and create a git commit "
            "in the current workspace."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "message": {
                    "type": "string",
                    "description": "Commit message.",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional paths to stage before commit.",
                },
            },
            "required": ["purpose", "message"],
            "additionalProperties": False,
        },
    },
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


def _run_git(args, *, cwd=None, timeout=DEFAULT_GIT_TIMEOUT_SECONDS):
    root = get_workspace_root()
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd or root,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        detail = output.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(detail)
    return output


def _ensure_git_repo():
    root = get_workspace_root()
    if not (root / ".git").exists():
        raise RuntimeError("Current workspace is not a git repository")


@tool("git_status")
def git_status():
    _ensure_git_repo()
    output = _run_git(["status", "--porcelain=v1", "-b"])
    output, truncated = truncate_output(output)
    return json.dumps({"status": output, "truncated": truncated}, ensure_ascii=False, indent=2)


@tool("git_diff")
def git_diff(path=None, staged=False):
    _ensure_git_repo()
    args = ["diff"]
    if staged:
        args.append("--cached")
    if path:
        resolve_workspace_path(path)
        args.extend(["--", path])
    output = _run_git(args)
    output, truncated = truncate_output(output)
    return json.dumps(
        {"path": path or ".", "staged": staged, "diff": output, "truncated": truncated},
        ensure_ascii=False,
        indent=2,
    )


@tool("git_commit")
def git_commit(message, paths=None):
    _ensure_git_repo()
    if not (message or "").strip():
        raise ValueError("message must not be empty")
    add_args = ["add"]
    if paths:
        for raw in paths:
            resolve_workspace_path(str(raw))
            add_args.append(str(raw))
    else:
        add_args.append("-A")
    _run_git(add_args)
    output = _run_git(["commit", "-m", message.strip()])
    output, truncated = truncate_output(output)
    return json.dumps({"message": message.strip(), "output": output, "truncated": truncated}, indent=2)
