import json
import subprocess
from pathlib import Path


WORKSPACE_ROOT = Path.cwd().resolve()
MAX_INSTALL_OUTPUT_CHARS = 20_000
DEFAULT_INSTALL_TIMEOUT_SECONDS = 300
MAX_INSTALL_TIMEOUT_SECONDS = 1200

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "install_python_packages",
        "description": "Install Python packages into the current project with uv add.",
        "parameters": {
            "type": "object",
            "properties": {
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Package specs to install, e.g. ['pytest'] or ['torch>=2'].",
                },
                "dev": {
                    "type": "boolean",
                    "description": "Install as development dependencies with uv add --dev.",
                    "default": True,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum time to wait before stopping the install.",
                    "default": DEFAULT_INSTALL_TIMEOUT_SECONDS,
                },
            },
            "required": ["packages"],
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


@tool("install_python_packages")
def install_python_packages(packages, dev=True, timeout_seconds=DEFAULT_INSTALL_TIMEOUT_SECONDS):
    packages = validate_package_specs(packages)
    timeout = max(1, min(int(timeout_seconds), MAX_INSTALL_TIMEOUT_SECONDS))
    command = ["uv", "add"]
    if dev:
        command.append("--dev")
    command.extend(packages)

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


def validate_package_specs(packages):
    if not isinstance(packages, list) or not packages:
        raise ValueError("packages must be a non-empty list")

    validated = []
    for package in packages:
        if not isinstance(package, str) or not package:
            raise ValueError("each package spec must be a non-empty string")
        if package.startswith("-") or any(char.isspace() for char in package):
            raise ValueError(f"invalid package spec: {package}")
        if "/" in package or "\\" in package:
            raise ValueError(f"package specs must be package names, not paths: {package}")
        validated.append(package)

    return validated


def truncate_output(output):
    if len(output) <= MAX_INSTALL_OUTPUT_CHARS:
        return output, False
    return output[:MAX_INSTALL_OUTPUT_CHARS] + "\n\n[truncated]", True
