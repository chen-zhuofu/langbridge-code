"""Approval policy: routine writes run freely; only high-risk operations ask.

File edits, commits, and ordinary shell commands are auto-approved — they are
easy to review and revert via git. User approval is required only for
operations with serious security implications or hard-to-reverse side effects
(recursive deletes, force pushes, elevated privileges, raw disk writes, …).

Two extra safety layers (inspired by Claude Code):
- Protected paths: writes into repo/agent state dirs (.git, .langbridge, …)
  always prompt, so the agent can't silently corrupt repository internals or
  its own configuration.
- Circuit breaker: removals targeting the filesystem root or home directory
  prompt even in yolo (auto-approve) mode.
"""
from __future__ import annotations

import re

SHELL_TOOL_NAMES = {"bash", "powershell"}

FILE_WRITE_TOOL_NAMES = {"write", "Edit"}

_PROTECTED_DIR_NAMES = {
    ".git",
    ".langbridge",
    ".langbridge-code",
    ".vscode",
    ".idea",
    ".cargo",
    ".husky",
}

_SHELL_HIGH_RISK_RULES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in (
        (r"(^|[;&|]\s*)sudo\b", "runs with elevated privileges (sudo)"),
        (r"(^|[;&|]\s*)su\s", "switches user (su)"),
        (r"\brm\b[^;|&]*(\s-\w*r|\s--recursive)", "recursive delete (rm -r)"),
        (r"\bfind\b[^;|&]*\s-delete\b", "bulk delete (find -delete)"),
        (
            r"\bgit\s+push\b[^;|&]*(\s--force\b|\s-f\b|\s--force-with-lease)",
            "force push rewrites remote history",
        ),
        (
            r"\bgit\s+reset\s+[^;|&]*--hard\b",
            "discards uncommitted work (git reset --hard)",
        ),
        (r"\bgit\s+clean\b", "deletes untracked files (git clean)"),
        (r"\|\s*(bash|sh|zsh|dash)\b", "pipes downloaded/script content into a shell"),
        (r"\bdd\b[^;|&]*\bof=", "raw write with dd"),
        (r"\bmkfs\b", "formats a filesystem"),
        (r"\bshred\b", "irrecoverable file shredding"),
        (r"\b(shutdown|reboot|poweroff|halt)\b", "system power control"),
        (r">\s*/dev/(sd|nvme|vd|hd)", "raw device write"),
        (r"\bchown\b[^;|&]*\s-\w*R", "recursive ownership change"),
        (r"\bremove-item\b[^;|&]*-recurse", "recursive delete (Remove-Item -Recurse)"),
        (r"\brmdir\s+/s\b", "recursive delete (rmdir /s)"),
        (r"\bdel\s+/[fq]\b", "forced delete (del /f)"),
        (r"\bformat(-volume)?\b\s", "formats a volume"),
    )
)

_ROOT_HOME_REMOVAL = re.compile(
    r"""\b(rm\s+(-\w+\s+)*-\w*r\w*|remove-item\b[^;|&]*-recurse[^;|&]*)\s+
        (--\s+)?["']?(/|~|\$HOME)/?\*?["']?\s*($|[;|&)`])""",
    re.IGNORECASE | re.VERBOSE,
)


def _protected_path_reason(name: str, arguments: dict | None) -> str | None:
    if name not in FILE_WRITE_TOOL_NAMES:
        return None
    path = str((arguments or {}).get("path", ""))
    if not path:
        return None
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    for part in parts:
        if part in _PROTECTED_DIR_NAMES:
            return f"writes inside protected directory {part}/"
    for first, second in zip(parts, parts[1:]):
        if first == ".config" and second == "git":
            return "writes inside protected directory .config/git/"
    return None


def circuit_breaker_reason(name: str, arguments: dict | None) -> str | None:
    if name not in SHELL_TOOL_NAMES:
        return None
    command = str((arguments or {}).get("command", ""))
    if _ROOT_HOME_REMOVAL.search(command):
        return "recursive removal of filesystem root or home directory"
    return None


def approval_reason(name: str, arguments: dict | None) -> str | None:
    protected = _protected_path_reason(name, arguments)
    if protected:
        return protected
    if name not in SHELL_TOOL_NAMES:
        return None
    command = str((arguments or {}).get("command", ""))
    for pattern, reason in _SHELL_HIGH_RISK_RULES:
        if pattern.search(command):
            return reason
    return None
