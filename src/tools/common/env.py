"""Sanitized environment for workspace subprocesses.

LangBridge usually runs inside its own venv. Without cleanup, workspace
commands inherit that VIRTUAL_ENV/PATH, so `python3` / `pip` inside the
user's project resolve to LangBridge's interpreter instead of the project's.
"""
import os
import shutil
import sys
from pathlib import Path

from langbridge_code.agents.common.workspace import get_workspace_root
from langbridge_code.tools.common.runtime import inject_runtime_env


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _strip_path_entries(path_value: str, prefix: Path) -> str:
    kept = [
        entry
        for entry in path_value.split(os.pathsep)
        if entry and not _is_inside(Path(entry), prefix)
    ]
    return os.pathsep.join(kept)


def workspace_env() -> dict:
    """os.environ with the host venv removed and the workspace .venv activated."""
    root = get_workspace_root()
    env = dict(os.environ)

    host_venv = env.get("VIRTUAL_ENV")
    if host_venv and not _is_inside(Path(host_venv), root):
        env.pop("VIRTUAL_ENV", None)
        env["PATH"] = _strip_path_entries(env.get("PATH", ""), Path(host_venv).resolve())

    inject_runtime_env(env)
    ws_venv = root / ".venv"
    bin_dir = ws_venv / ("Scripts" if os.name == "nt" else "bin")
    if bin_dir.is_dir():
        env["VIRTUAL_ENV"] = str(ws_venv)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


def workspace_python(env: dict | None = None) -> str:
    """Interpreter for workspace test runs: workspace .venv first, else host python."""
    env = env if env is not None else workspace_env()
    root = get_workspace_root()
    for candidate in (
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return str(candidate)
    found = shutil.which("python3", path=env.get("PATH")) or shutil.which(
        "python", path=env.get("PATH")
    )
    return found or sys.executable
