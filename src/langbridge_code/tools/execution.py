import json
import subprocess
from pathlib import Path

from langbridge_cli.settings import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    MAX_EXECUTION_OUTPUT_CHARS,
    MAX_EXECUTION_TIMEOUT_SECONDS,
)

WORKSPACE_ROOT = Path.cwd().resolve()

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "bash",
        "description": (
            "Run a non-interactive shell command under the current workspace "
            "(via bash -c). Use for installs (e.g. uv add pytest), builds, "
            "git, and one-off scripts. Pipes and && are allowed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
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
            "required": ["command"],
            "additionalProperties": False,
        },
    }
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


def resolve_workspace_path(path):
    target = (WORKSPACE_ROOT / path).resolve()
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise ValueError("Path must stay inside the current workspace")
    return target


@tool("bash")
def bash(command, cwd=".", timeout_seconds=DEFAULT_EXECUTION_TIMEOUT_SECONDS):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")

    target_cwd = resolve_workspace_path(cwd)
    if not target_cwd.exists():
        raise FileNotFoundError(f"No such working directory: {cwd}")
    if not target_cwd.is_dir():
        raise NotADirectoryError(f"Not a directory: {cwd}")

    timeout = max(1, min(int(timeout_seconds), MAX_EXECUTION_TIMEOUT_SECONDS))

    try:
        completed = subprocess.run(
            ["bash", "-c", command],
            cwd=target_cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout
        timed_out = False
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        timed_out = True
        exit_code = None

    output, truncated = truncate_output(output)
    return json.dumps(
        {
            "command": command,
            "cwd": str(target_cwd.relative_to(WORKSPACE_ROOT)),
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
