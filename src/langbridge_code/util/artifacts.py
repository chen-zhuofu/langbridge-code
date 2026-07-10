"""Artifact session paths: langbridge_code/artifacts/session-{slug}-{timestamp}/."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from langbridge_code.settings import ARTIFACTS_DIR

SESSION_JSON = "session.json"
PROGRESS_MD = "progress.md"
TODO_LIST_MD = "todo_list.md"
TRACES_DIRNAME = "traces"
DEBUG_DIRNAME = "debug"

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
    if run_log_path is None:
        return None
    path = Path(run_log_path)
    if path.name == SESSION_JSON:
        return path.parent
    return path.parent if path.suffix == ".json" else path


def session_json_path(session_dir: Path) -> Path:
    return session_dir / SESSION_JSON


def progress_path(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / PROGRESS_MD


def todo_list_path(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / TODO_LIST_MD


def traces_dir(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / TRACES_DIRNAME


def debug_dir(run_log_path) -> Path | None:
    directory = artifact_dir(run_log_path)
    if directory is None:
        return None
    return directory / DEBUG_DIRNAME


def debug_trace_dir(run_log_path, trace_id: str) -> Path | None:
    base = debug_dir(run_log_path)
    if base is None or not trace_id:
        return None
    return base / trace_id


def create_artifact_session(first_user_message: str, when: datetime | None = None) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    name = session_dir_name(first_user_message, when=when)
    session_dir = ARTIFACTS_DIR / name
    suffix = 1
    while session_dir.exists():
        session_dir = ARTIFACTS_DIR / f"{name}-{suffix}"
        suffix += 1
    session_dir.mkdir(parents=True)
    (session_dir / TRACES_DIRNAME).mkdir(exist_ok=True)
    (session_dir / DEBUG_DIRNAME).mkdir(exist_ok=True)
    path = session_json_path(session_dir)
    path.write_text(
        json.dumps({"summary": "", "turns": []}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def list_artifact_sessions() -> list[Path]:
    if not ARTIFACTS_DIR.exists():
        return []
    paths = []
    for session_dir in ARTIFACTS_DIR.glob("session-*"):
        if not session_dir.is_dir():
            continue
        session_json = session_dir / SESSION_JSON
        if session_json.is_file():
            paths.append(session_json)
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def label_artifact_session(session_json_path: Path) -> str:
    from langbridge_code.util.session import read_session_log

    try:
        session_log = read_session_log(session_json_path)
        summary = session_log.get("summary") or ""
    except (OSError, json.JSONDecodeError):
        summary = "unreadable session"
    folder = session_json_path.parent.name
    if summary:
        return f"{folder} — {summary}"
    return folder


def agent_file_prefix(label: str, instance_id: int | None) -> str:
    slug = label.lower().replace(" ", "_")
    if instance_id is None:
        return slug
    return f"{slug}_{instance_id}"
