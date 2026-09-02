"""Artifact session paths under artifacts/{project}/session-{slug}-{timestamp}/.

Layout:
  todo_list.md         main-agent plan (main only)
  session_memory.md    main-agent session memory (main only; legacy: progress.md)
  traces.md            main-agent raw trace (engine/human only)
  session.md           activity log (engine/human only)
  attachments/         oversized payloads linked from session.md (engine/human only)
  main/
    attachments/       main-agent bash/webpage spills (main only)
  tasks/{task-slug}/
    traces/            {role}-{n}.md raw traces (engine/human only)
    worker/
      session_memory.md
      attachments/
    reviewer/
      session_memory.md
      attachments/
    explorer/
      session_memory.md
      attachments/
      reports/
        report-{n}.md  explorer findings for main to read
    planner/
      attachments/
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from langbridge_code.settings import ARTIFACTS_DIR

SESSION_MEMORY_MD = "session_memory.md"
LEGACY_PROGRESS_MD = "progress.md"
# Back-compat alias for older imports.
PROGRESS_MD = SESSION_MEMORY_MD
TRACES_MD = "traces.md"
SESSION_TRACE_MD = "session.md"
SESSION_TITLE_FILE = ".session-title"
ATTACHMENTS_DIRNAME = "attachments"
TASKS_DIRNAME = "tasks"
TRACES_DIRNAME = "traces"
REPORTS_DIRNAME = "reports"
MAIN_DIRNAME = "main"

_INVALID_PATH_CHARS = re.compile(r'[/\\:*?"<>|\s]+')
_SESSION_DIR_RE = re.compile(r"^session-.+-(\d{4}-\d{2}-\d{2}T\d{6})$")

_ROLE_DIR_ALIASES = {
    "langbridge": MAIN_DIRNAME,
    "main": MAIN_DIRNAME,
    "worker": "worker",
    "reviewer": "reviewer",
    "explore": "explorer",
    "explorer": "explorer",
    "planner": "planner",
}


def slug_first_message(text: str, *, max_len: int = 40) -> str:
    compact = " ".join((text or "").split()).strip()
    if not compact:
        return "untitled"
    slug = _INVALID_PATH_CHARS.sub("-", compact)
    slug = slug.strip("-")
    if not slug:
        return "untitled"
    return slug[:max_len].rstrip("-") or "untitled"


def role_dir_name(role: str) -> str:
    """Map an agent label to its artifact directory name."""
    key = (role or "").strip().lower()
    if key in _ROLE_DIR_ALIASES:
        return _ROLE_DIR_ALIASES[key]
    return slug_first_message(role or "agent").lower()


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


def session_memory_path(run_log_path) -> Path | None:
    """Canonical write path for session memory (session_memory.md)."""
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / SESSION_MEMORY_MD


def resolve_session_memory_path(run_log_path) -> Path | None:
    """Read path: prefer session_memory.md, else legacy progress.md."""
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    preferred = directory / SESSION_MEMORY_MD
    if preferred.exists():
        return preferred
    legacy = directory / LEGACY_PROGRESS_MD
    if legacy.exists():
        return legacy
    return preferred


def progress_path(run_log_path) -> Path | None:
    """Back-compat alias for ``session_memory_path`` (write target)."""
    return session_memory_path(run_log_path)


def main_dir(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / MAIN_DIRNAME


def main_attachments_dir(run_log_path) -> Path | None:
    directory = main_dir(run_log_path)
    if directory is None:
        return None
    return directory / ATTACHMENTS_DIRNAME


def task_dir(run_log_path, task_name: str) -> Path | None:
    """Per-task directory: {session}/tasks/{task-slug}/."""
    directory = artifact_dir(run_log_path)
    if directory is None or not (task_name or "").strip():
        return None
    return directory / TASKS_DIRNAME / slug_first_message(task_name)


def task_role_dir(run_log_path, task_name: str, role: str) -> Path | None:
    """Role-scoped task dir: {session}/tasks/{slug}/{role}/."""
    directory = task_dir(run_log_path, task_name)
    if directory is None:
        return None
    return directory / role_dir_name(role)


def task_session_memory_path(
    run_log_path, task_name: str, role: str = "Worker"
) -> Path | None:
    """Per-task, per-role session memory write path (session_memory.md)."""
    directory = task_role_dir(run_log_path, task_name, role)
    if directory is None:
        return None
    return directory / SESSION_MEMORY_MD


def resolve_task_session_memory_path(
    run_log_path, task_name: str, role: str = "Worker"
) -> Path | None:
    """Read path: prefer session_memory.md, else legacy progress.md."""
    directory = task_role_dir(run_log_path, task_name, role)
    if directory is None:
        return None
    preferred = directory / SESSION_MEMORY_MD
    if preferred.exists():
        return preferred
    legacy = directory / LEGACY_PROGRESS_MD
    if legacy.exists():
        return legacy
    return preferred


def task_progress_path(run_log_path, task_name: str, role: str = "Worker") -> Path | None:
    """Back-compat alias for ``task_session_memory_path`` (write target)."""
    return task_session_memory_path(run_log_path, task_name, role)


def task_attachments_dir(run_log_path, task_name: str, role: str) -> Path | None:
    directory = task_role_dir(run_log_path, task_name, role)
    if directory is None:
        return None
    return directory / ATTACHMENTS_DIRNAME


def task_traces_dir(run_log_path, task_name: str) -> Path | None:
    """Engine-only raw traces for a task: {session}/tasks/{slug}/traces/."""
    directory = task_dir(run_log_path, task_name)
    if directory is None:
        return None
    return directory / TRACES_DIRNAME


def explorer_reports_dir(run_log_path, task_name: str) -> Path | None:
    directory = task_role_dir(run_log_path, task_name, "Explore")
    if directory is None:
        return None
    return directory / REPORTS_DIRNAME


def explorer_report_path(run_log_path, task_name: str, instance_id: int) -> Path | None:
    directory = explorer_reports_dir(run_log_path, task_name)
    if directory is None or instance_id is None:
        return None
    return directory / f"report-{instance_id}.md"


def traces_md_path(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / TRACES_MD


def attachments_dir(run_log_path) -> Path | None:
    """Session.md log attachments (engine/human only — not agent-readable)."""
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / ATTACHMENTS_DIRNAME


def agent_attachments_dir(run_log_path, *, role: str, task_name: str = "") -> Path | None:
    """Directory for bash/webpage spills owned by one agent."""
    role_key = role_dir_name(role)
    if role_key == MAIN_DIRNAME:
        return main_attachments_dir(run_log_path)
    return task_attachments_dir(run_log_path, task_name, role_key)


def session_trace_path(run_log_path) -> Path | None:
    """Single human-readable trace log for the whole session (session.md)."""
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / SESSION_TRACE_MD


def is_engine_only_path(path: Path, session_dir: Path) -> bool:
    """True for traces/session logs that agents must not read_file."""
    resolved = Path(path).resolve()
    session = Path(session_dir).resolve()
    try:
        rel = resolved.relative_to(session)
    except ValueError:
        return False
    if resolved == session / TRACES_MD or resolved == session / SESSION_TRACE_MD:
        return True
    if rel.parts and rel.parts[0] == ATTACHMENTS_DIRNAME:
        return True
    return TRACES_DIRNAME in rel.parts


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
    (session_dir / MAIN_DIRNAME).mkdir()
    (session_dir / SESSION_MEMORY_MD).write_text("# Session memory\n", encoding="utf-8")
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
    directory = path if path.is_dir() else path.parent
    title_path = directory / SESSION_TITLE_FILE
    if title_path.is_file():
        title = " ".join(title_path.read_text(encoding="utf-8").split()).strip()
        if title:
            return title
    return directory.name.removeprefix("session-")


def rename_artifact_session(session_path: Path, title: str) -> str:
    directory = Path(session_path)
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    normalized = " ".join((title or "").split()).strip()[:120].rstrip()
    if not normalized:
        raise ValueError("Session name cannot be empty.")
    (directory / SESSION_TITLE_FILE).write_text(normalized + "\n", encoding="utf-8")
    return normalized
