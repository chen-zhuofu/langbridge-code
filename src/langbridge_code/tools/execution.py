import json
import re
import shutil
from pathlib import Path

from langbridge_code.settings import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    MAX_EXECUTION_OUTPUT_CHARS,
    MAX_EXECUTION_TIMEOUT_SECONDS,
)
from langbridge_code.tools.common.env import workspace_env
from langbridge_code.tools.common.proc import run_command
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER
from langbridge_code.tools.common.runtime import managed_binary
from langbridge_code.agents.common.workspace import get_workspace_root

WORKSPACE_ROOT = Path.cwd().resolve()

_PRIVILEGED_COMMAND_RE = re.compile(r"\b(sudo|su|doas|pkexec)\b", re.IGNORECASE)
_WRITE_BASH_PATTERN = re.compile(
    r"(^|[;&|]\s*)(rm\s|rmdir\s|mv\s|cp\s|touch\s|mkdir\s|"
    r"chmod\s|chown\s|tee\s|truncate\s|>"
    r"|>>\s|sed\s+-i|git\s+(add|commit|push|checkout\s+-b|merge|rebase|reset|clean)|"
    r"pip\s+install|uv\s+add|npm\s+install|yarn\s+add|cargo\s+install)",
    re.IGNORECASE,
)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "bash",
        "description": (
            "Run a non-interactive shell command under the current workspace "
            "(via bash -c). Use for installs (e.g. uv add pytest), builds, "
            "git (status, log, branch), and one-off scripts. "
            "Main agent: inspect git state; delegate merges to agent_worker. "
            "Pipes and && are allowed. Prefer write/Edit for file content. "
            "sudo/su/doas/pkexec are blocked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "command": {
                    "type": "string",
                    "description": "Shell command to run, e.g. 'uv add pytest' or 'python -m pytest tests/ -q'.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to the current workspace.",
                    "default": ".",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum time to wait before stopping the command.",
                    "default": DEFAULT_EXECUTION_TIMEOUT_SECONDS,
                },
            },
            "required": ["purpose", "command"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "powershell",
        "description": (
            "Run a non-interactive PowerShell command under the current workspace "
            "(via pwsh -Command). Use on Windows-oriented scripts or when pwsh is available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "command": {
                    "type": "string",
                    "description": "PowerShell command to run.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to the current workspace.",
                    "default": ".",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum time to wait before stopping the command.",
                    "default": DEFAULT_EXECUTION_TIMEOUT_SECONDS,
                },
            },
            "required": ["purpose", "command"],
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


def resolve_workspace_path(path):
    target = (get_workspace_root() / path).resolve()
    try:
        target.relative_to(get_workspace_root())
    except ValueError:
        raise ValueError("Path must stay inside the current workspace")
    return target


def reject_privileged_command(command: str) -> None:
    if _PRIVILEGED_COMMAND_RE.search(command or ""):
        raise ValueError(
            "Privileged commands (sudo, su, doas, pkexec) are not allowed. "
            "Use non-interactive commands that do not require root."
        )


def bash_write_guard(command: str, *, role: str = "agent") -> str | None:
    """Return an error when a shell command would mutate the workspace."""
    cleaned = (command or "").strip()
    if not cleaned:
        return None
    if _WRITE_BASH_PATTERN.search(cleaned):
        return f"{role} may only run read-only shell commands."
    return None


def read_only_bash(*, role: str = "agent", **kwargs):
    """Run bash after rejecting write/mutate commands."""
    guard_error = bash_write_guard(kwargs.get("command") or "", role=role)
    if guard_error:
        raise PermissionError(guard_error)
    return TOOLS["bash"](**kwargs)


@tool("bash")
def bash(command, cwd=".", timeout_seconds=DEFAULT_EXECUTION_TIMEOUT_SECONDS):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    reject_privileged_command(command)

    target_cwd = resolve_workspace_path(cwd)
    if not target_cwd.exists():
        raise FileNotFoundError(f"No such working directory: {cwd}")
    if not target_cwd.is_dir():
        raise NotADirectoryError(f"Not a directory: {cwd}")

    timeout = max(1, min(int(timeout_seconds), MAX_EXECUTION_TIMEOUT_SECONDS))

    output, exit_code, timed_out = run_command(
        [managed_binary("bash"), "-c", command],
        cwd=target_cwd,
        env=workspace_env(),
        timeout=timeout,
    )
    output, truncated = truncate_output(output)
    return json.dumps(
        {
            "command": command,
            "cwd": str(target_cwd.relative_to(get_workspace_root())),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "truncated": truncated,
            "output": output,
        },
        ensure_ascii=False,
        indent=2,
    )


def _powershell_binary():
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("PowerShell (pwsh or powershell) was not found on PATH.")


@tool("powershell")
def powershell(command, cwd=".", timeout_seconds=DEFAULT_EXECUTION_TIMEOUT_SECONDS):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    reject_privileged_command(command)

    target_cwd = resolve_workspace_path(cwd)
    if not target_cwd.exists():
        raise FileNotFoundError(f"No such working directory: {cwd}")
    if not target_cwd.is_dir():
        raise NotADirectoryError(f"Not a directory: {cwd}")

    timeout = max(1, min(int(timeout_seconds), MAX_EXECUTION_TIMEOUT_SECONDS))
    binary = _powershell_binary()

    output, exit_code, timed_out = run_command(
        [binary, "-NoProfile", "-Command", command],
        cwd=target_cwd,
        env=workspace_env(),
        timeout=timeout,
    )
    output, truncated = truncate_output(output)
    return json.dumps(
        {
            "command": command,
            "cwd": str(target_cwd.relative_to(get_workspace_root())),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "truncated": truncated,
            "output": output,
        },
        ensure_ascii=False,
        indent=2,
    )


def truncate_output(output):
    if len(output) <= MAX_EXECUTION_OUTPUT_CHARS:
        return output, False
    return output[:MAX_EXECUTION_OUTPUT_CHARS] + "\n\n[truncated]", True
