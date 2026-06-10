import json
import subprocess
from pathlib import Path


WORKSPACE_ROOT = Path.cwd().resolve()
MAX_EXECUTION_OUTPUT_CHARS = 20_000
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 60
MAX_EXECUTION_TIMEOUT_SECONDS = 300

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "execute_program",
        "description": "Execute a non-interactive program under the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "program": {
                    "type": "string",
                    "description": "Program to execute, e.g. 'python', 'uv', or 'git'.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Program arguments. Do not include the program name.",
                    "default": [],
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to the current workspace.",
                    "default": ".",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum time to wait before stopping the program.",
                    "default": DEFAULT_EXECUTION_TIMEOUT_SECONDS,
                },
            },
            "required": ["program"],
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


@tool("execute_program")
def execute_program(program, args=None, cwd=".", timeout_seconds=DEFAULT_EXECUTION_TIMEOUT_SECONDS):
    if not isinstance(program, str) or not program:
        raise ValueError("program must be a non-empty string")

    args = validate_args(args or [])
    target_cwd = resolve_workspace_path(cwd)
    if not target_cwd.exists():
        raise FileNotFoundError(f"No such working directory: {cwd}")
    if not target_cwd.is_dir():
        raise NotADirectoryError(f"Not a directory: {cwd}")

    timeout = max(1, min(int(timeout_seconds), MAX_EXECUTION_TIMEOUT_SECONDS))
    command = [program] + args

    try:
        completed = subprocess.run(
            command,
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


def validate_args(args):
    if not isinstance(args, list):
        raise ValueError("args must be a list")

    validated = []
    for arg in args:
        if not isinstance(arg, str):
            raise ValueError("each arg must be a string")
        validated.append(arg)
    return validated


def truncate_output(output):
    if len(output) <= MAX_EXECUTION_OUTPUT_CHARS:
        return output, False
    return output[:MAX_EXECUTION_OUTPUT_CHARS] + "\n\n[truncated]", True
