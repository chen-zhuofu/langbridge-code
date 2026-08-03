import itertools
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
from langbridge_code.tools.common.description import DESCRIPTION_PARAMETER
from langbridge_code.tools.common.runtime import managed_binary
from langbridge_code.agents.common.workspace import get_workspace_root

WORKSPACE_ROOT = Path.cwd().resolve()

# Claude Code v2.1.2+: spill full output to disk; context keeps a short head preview.
TOOL_OUTPUT_PREVIEW_CHARS = 2_000
EXECUTION_OUTPUT_PREVIEW_CHARS = TOOL_OUTPUT_PREVIEW_CHARS  # back-compat alias
_tool_output_seq = itertools.count(1)
_SHELL_SPILL_TOOLS = frozenset({"bash", "powershell"})
_SPILL_TOOLS = _SHELL_SPILL_TOOLS | frozenset({"read_webpage"})

_PRIVILEGED_COMMAND_RE = re.compile(r"\b(sudo|su|doas|pkexec)\b", re.IGNORECASE)
_WRITE_BASH_PATTERN = re.compile(
    r"(^|[;&|]\s*)(rm\s|rmdir\s|mv\s|cp\s|touch\s|mkdir\s|"
    r"chmod\s|chown\s|tee\s|truncate\s|"
    r"sed\s+-i|git\s+(add|commit|push|checkout\s+-b|merge|rebase|reset|clean)|"
    r"pip\s+install|uv\s+add|npm\s+install|yarn\s+add|cargo\s+install)",
    re.IGNORECASE,
)
# Redirects that do not write files: fd duplication (2>&1) and /dev/null.
_HARMLESS_REDIRECT = re.compile(r"\d?>>?\s*(&\d|/dev/null)")

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "bash",
        "description": (
            "Run a non-interactive shell command under the current workspace "
            "(via bash -c). Use for installs (e.g. uv add pytest), builds, "
            "git (status, log, branch), and one-off scripts. "
            "Feature-branch merges use merge_branch (not agent_worker). "
            "Pipes and && are allowed. Prefer write/Edit for file content. "
            "sudo/su/doas/pkexec are blocked. Do not run broad destructive "
            "commands (e.g. rm -rf on home/workspace roots, git reset --hard, "
            "force-push) unless the user clearly asked for that exact "
            "operation; if the target or scope is unclear, ask first. "
            "Oversized stdout/stderr is saved in full under this agent's "
            "session attachments/; the tool result only keeps a short head "
            "preview plus the absolute path — use read_file with offset/limit "
            "when you need more. A session directory is required."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": DESCRIPTION_PARAMETER,
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
            "required": ["description", "command"],
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
                "description": DESCRIPTION_PARAMETER,
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
            "required": ["description", "command"],
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
    if ">" in _HARMLESS_REDIRECT.sub("", cleaned):
        return f"{role} may only run read-only shell commands (file redirect)."
    return None


def read_only_bash(*, role: str = "agent", **kwargs):
    """Run bash after rejecting write/mutate commands."""
    guard_error = bash_write_guard(kwargs.get("command") or "", role=role)
    if guard_error:
        raise PermissionError(guard_error)
    return TOOLS["bash"](**kwargs)


def attach_run_log_path(name: str, arguments: dict, run_log_path) -> None:
    """Inject session dir so oversized tool outputs can spill into attachments/."""
    if run_log_path is not None and name in _SPILL_TOOLS:
        arguments["run_log_path"] = run_log_path


@tool("bash")
def bash(
    command,
    cwd=".",
    timeout_seconds=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    run_log_path=None,
):
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
    return _execution_result(
        command=command,
        target_cwd=target_cwd,
        exit_code=exit_code,
        timed_out=timed_out,
        output=output,
        run_log_path=run_log_path,
    )


def _powershell_binary():
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("PowerShell (pwsh or powershell) was not found on PATH.")


@tool("powershell")
def powershell(
    command,
    cwd=".",
    timeout_seconds=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    run_log_path=None,
):
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
    return _execution_result(
        command=command,
        target_cwd=target_cwd,
        exit_code=exit_code,
        timed_out=timed_out,
        output=output,
        run_log_path=run_log_path,
    )


def _execution_result(*, command, target_cwd, exit_code, timed_out, output, run_log_path=None):
    inline, truncated, output_path = prepare_execution_output(
        output, run_log_path=run_log_path
    )
    payload = {
        "command": command,
        "cwd": str(target_cwd.relative_to(get_workspace_root())),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "truncated": truncated,
        "output": inline,
    }
    if output_path is not None:
        payload["output_path"] = str(output_path)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _resolve_spill_directory(run_log_path=None) -> Path:
    """Current agent's session attachments/ — session is required."""
    from langbridge_code.agents.common.workspace import (
        get_agent_label,
        get_agent_task_name,
    )
    from langbridge_code.util.artifacts import agent_attachments_dir
    from langbridge_code.util.trace_log import get_trace_context

    explicit = run_log_path
    if explicit is None:
        ctx = get_trace_context()
        if ctx is not None:
            explicit = ctx.run_log_path
    if explicit is None:
        raise RuntimeError(
            "No session directory; oversized tool output cannot be saved."
        )
    label = get_agent_label() or "LangBridge"
    task_name = get_agent_task_name()
    directory = agent_attachments_dir(
        explicit, role=label, task_name=task_name
    )
    if directory is None:
        raise RuntimeError(
            "No session directory; oversized tool output cannot be saved."
        )
    return directory


def persist_tool_output(text: str, *, run_log_path=None, kind: str = "tool-output") -> Path:
    """Write full tool output to this agent's attachments/; return the path."""
    from langbridge_code.util.artifacts import format_trace_timestamp

    safe_kind = re.sub(r"[^A-Za-z0-9._-]+", "-", (kind or "tool-output").strip()) or "tool-output"
    directory = _resolve_spill_directory(run_log_path)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{safe_kind}-{format_trace_timestamp()}-{next(_tool_output_seq):03d}.txt"
    path = (directory / name).resolve()
    path.write_text(text, encoding="utf-8")
    return path


def prepare_tool_output(
    output,
    *,
    run_log_path=None,
    max_chars: int | None = None,
    kind: str = "tool-output",
    preview_chars: int = TOOL_OUTPUT_PREVIEW_CHARS,
):
    """Return (inline_text, truncated, output_path_or_none).

    Under the spill threshold the full text stays inline. Over it, the full
    text is persisted and the model only sees a short head preview + path.
    """
    text = output if isinstance(output, str) else ("" if output is None else str(output))
    threshold = MAX_EXECUTION_OUTPUT_CHARS if max_chars is None else max(1, int(max_chars))
    if len(text) <= threshold:
        return text, False, None
    path = persist_tool_output(text, run_log_path=run_log_path, kind=kind)
    preview = text[: max(1, int(preview_chars))]
    notice = (
        f"\n\n[output truncated — full output saved to {path} "
        f"({len(text)} chars). Use read_file on that path with offset/limit "
        f"if you need more.]"
    )
    return preview + notice, True, path


def persist_execution_output(text: str, *, run_log_path=None) -> Path:
    """Write full command output to a readable path; return the absolute path."""
    return persist_tool_output(text, run_log_path=run_log_path, kind="bash-output")


def prepare_execution_output(output, *, run_log_path=None):
    """Return (inline_text, truncated, output_path_or_none) for shell tools."""
    return prepare_tool_output(
        output,
        run_log_path=run_log_path,
        max_chars=MAX_EXECUTION_OUTPUT_CHARS,
        kind="bash-output",
    )


def truncate_output(output):
    """Back-compat: return (inline, truncated) without exposing the spill path."""
    inline, truncated, _path = prepare_execution_output(output)
    return inline, truncated
