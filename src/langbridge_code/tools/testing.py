import json
import subprocess
import sys
from pathlib import Path

from langbridge_cli.settings import (
    DEFAULT_TEST_TIMEOUT_SECONDS,
    MAX_TEST_OUTPUT_CHARS,
    MAX_TEST_TIMEOUT_SECONDS,
)

WORKSPACE_ROOT = Path.cwd().resolve()

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "run_tests",
        "description": "Run Python unit tests under the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Test file or directory path relative to the current workspace.",
                    "default": ".",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum time to wait before stopping tests.",
                    "default": DEFAULT_TEST_TIMEOUT_SECONDS,
                },
            },
            "required": [],
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


@tool("run_tests")
def run_tests(path=".", timeout_seconds=DEFAULT_TEST_TIMEOUT_SECONDS):
    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such test path: {path}")

    timeout = max(1, min(int(timeout_seconds), MAX_TEST_TIMEOUT_SECONDS))
    command = [sys.executable, "-m", "pytest", str(target.relative_to(WORKSPACE_ROOT))]

    try:
        completed = subprocess.run(
            command,
            cwd=WORKSPACE_ROOT,
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
            "exit_code": exit_code,
            "timed_out": timed_out,
            "truncated": truncated,
            "output": output,
        },
        ensure_ascii=False,
        indent=2,
    )


def truncate_output(output):
    if len(output) <= MAX_TEST_OUTPUT_CHARS:
        return output, False
    return output[:MAX_TEST_OUTPUT_CHARS] + "\n\n[truncated]", True
