"""Artifact session paths under artifacts/{project}/session-{slug}-{timestamp}/.

Layout:
  progress.md          main-agent progress note
  traces.md            main-agent raw trace
  session.md           activity log all agents write to
  attachments/         oversized payloads linked from session.md / audit events
  compactions.jsonl    progress-note merge audit
  {task-slug}/         one directory per subagent task
    progress.md        task note (shared across dispatches and roles)
    {role}-{n}.md      raw trace of dispatch instance n
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from langbridge_code.settings import ARTIFACTS_DIR

PROGRESS_MD = "progress.md"
TRACES_MD = "traces.md"
SESSION_TRACE_MD = "session.md"
ATTACHMENTS_DIRNAME = "attachments"

_INVALID_PATH_CHARS = re.compile(r'[/\\:*?"<>|\s]+')
_SESSION_DIR_RE = re.compile(r"^session-.+-(\d{4}-\d{2}-\d{2}T\d{6})$")


def slug_first_message(text: str, *, max_len: int = 40) -> str:
    compact = " ".join((text or "").split()).strip()
    if not compact:
        return "untitled"
    slug = _INVALID_PATH_CHARS.sub("-", compact)
    slug = slug.strip("-")
    if not slug:
        return "untitled"
    return slug[:max_len].rstrip("-") or "untitled"


def format_session_timestamp(when: datetime | None = None) -> str:
    moment = when or datetime.now()
    return moment.strftime("%Y-%m-%dT%H%M%S")


def format_trace_timestamp(when: datetime | None = None) -> str:
    moment = when or datetime.now()
    centis = moment.microsecond // 10_000
    return f"{format_session_timestamp(moment)}.{centis:02d}"


def format_line_timestamp(when: datetime | None = None) -> str:
    moment = when or datetime.now()
    centis = moment.microsecond // 10_000
    return moment.strftime("%H:%M:%S") + f".{centis:02d}"


def session_dir_name(first_user_message: str, when: datetime | None = None) -> str:
    return f"session-{slug_first_message(first_user_message)}-{format_session_timestamp(when)}"


def artifact_dir(run_log_path) -> Path | None:
    """Resolve the session artifact directory from a run_log_path (the session dir)."""
    if run_log_path is None:
        return None
    return Path(run_log_path)


def progress_path(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / PROGRESS_MD


def task_dir(run_log_path, task_name: str) -> Path | None:
    """Per-task directory for subagent artifacts: {session}/{task-slug}/."""
    directory = artifact_dir(run_log_path)
    if directory is None or not (task_name or "").strip():
        return None
    return directory / slug_first_message(task_name)


def task_progress_path(run_log_path, task_name: str) -> Path | None:
    """Per-task progress notes for subagents: {session}/{task-slug}/progress.md.

    The same task_name across re-dispatches maps to the same file, so a
    later subagent resumes from the earlier one's notes.
    """
    directory = task_dir(run_log_path, task_name)
    if directory is None:
        return None
    return directory / PROGRESS_MD


def traces_md_path(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / TRACES_MD


def attachments_dir(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / ATTACHMENTS_DIRNAME


def session_trace_path(run_log_path) -> Path | None:
    """Single human-readable trace log for the whole session (session.md)."""
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / SESSION_TRACE_MD


def create_artifact_session(first_user_message: str, when: datetime | None = None) -> Path:
    """Create a session artifact directory and return its path."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    name = session_dir_name(first_user_message, when=when)
    session_dir = ARTIFACTS_DIR / name
    suffix = 1
    while session_dir.exists():
        session_dir = ARTIFACTS_DIR / f"{name}-{suffix}"
        suffix += 1
    session_dir.mkdir(parents=True)
    (session_dir / PROGRESS_MD).write_text("# Session progress\n", encoding="utf-8")
    (session_dir / TRACES_MD).write_text("# Session traces\n", encoding="utf-8")
    return session_dir


def list_artifact_sessions() -> list[Path]:
    """Return session directories, newest first."""
    if not ARTIFACTS_DIR.exists():
        return []
    paths = []
    for session_dir in ARTIFACTS_DIR.glob("session-*"):
        if session_dir.is_dir():
            paths.append(session_dir)
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def label_artifact_session(session_path: Path) -> str:
    path = Path(session_path)
    return path.name if path.is_dir() else path.parent.name
