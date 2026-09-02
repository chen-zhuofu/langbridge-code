"""Persistent schedule definitions shared by the agent tool and macOS runner."""
from __future__ import annotations

import fcntl
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path

from croniter import croniter

from langbridge_code.util.app_paths import app_support_dir

SCHEDULE_VERSION = 1
SAFE_SCHEDULED_TOOLS = frozenset(
    {"browser", "gmail", "glob", "grep", "read_file", "read_webpage", "read_skill"}
)
RECURRENCES = frozenset({"once", "daily", "weekdays", "weekly", "cron"})


def ensure_private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def schedules_dir() -> Path:
    return app_support_dir() / "schedules"


def schedule_runs_dir() -> Path:
    return app_support_dir() / "schedule-runs"


def schedule_logs_dir() -> Path:
    return app_support_dir() / "logs"


def preferences_path() -> Path:
    return app_support_dir() / "preferences.json"


def load_preferences() -> dict:
    try:
        value = json.loads(preferences_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def obsidian_vault_path() -> Path | None:
    raw = str(load_preferences().get("obsidian_vault_path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    return path if path.is_dir() else None


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_clock(value: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour=hour, minute=minute)
    except (TypeError, ValueError) as error:
        raise ValueError("time must use 24-hour HH:MM format.") from error


def _next_matching_day(now: datetime, clock: time, allowed_weekdays: set[int]) -> datetime:
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        candidate = datetime.combine(day, clock, tzinfo=now.tzinfo)
        if day.weekday() in allowed_weekdays and candidate > now:
            return candidate
    raise ValueError("Could not calculate the next run time.")


def compute_next_run(recurrence: str, spec: dict, *, now: datetime | None = None) -> str:
    current = now or _now()
    kind = str(recurrence or "").strip().lower()
    if kind not in RECURRENCES:
        raise ValueError(f"recurrence must be one of: {', '.join(sorted(RECURRENCES))}.")
    if kind == "once":
        try:
            candidate = datetime.fromisoformat(str(spec.get("run_at") or ""))
        except ValueError as error:
            raise ValueError("run_at must be an ISO date and time.") from error
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=current.tzinfo)
        if candidate <= current:
            raise ValueError("run_at must be in the future.")
    elif kind == "cron":
        expression = str(spec.get("cron") or "").strip()
        if not croniter.is_valid(expression):
            raise ValueError("cron must be a valid five-field cron expression.")
        candidate = croniter(expression, current).get_next(datetime)
    else:
        clock = _parse_clock(str(spec.get("time") or ""))
        if kind == "daily":
            weekdays = set(range(7))
        elif kind == "weekdays":
            weekdays = set(range(5))
        else:
            try:
                weekday = int(spec.get("weekday"))
            except (TypeError, ValueError) as error:
                raise ValueError("weekday must be 0 (Monday) through 6 (Sunday).") from error
            if weekday not in range(7):
                raise ValueError("weekday must be 0 (Monday) through 6 (Sunday).")
            weekdays = {weekday}
        candidate = _next_matching_day(current, clock, weekdays)
    return candidate.isoformat(timespec="seconds")


def _clean_name(value: str) -> str:
    name = " ".join(str(value or "").split()).strip()
    if not name:
        raise ValueError("name is required.")
    return name[:120]


def _clean_output_subdirectory(value: str, name: str) -> str:
    component = re.sub(r"[/:]", "-", name).strip(" .") or "Scheduled Task"
    raw = str(value or "").strip() or f"LangBridge/{component}"
    path = Path(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("output_subdirectory must stay inside the selected Obsidian Vault.")
    return str(path)


def _clean_tools(values) -> list[str]:
    tools = list(dict.fromkeys(str(value).strip() for value in (values or []) if str(value).strip()))
    unknown = sorted(set(tools) - SAFE_SCHEDULED_TOOLS)
    if unknown:
        raise ValueError(
            "Scheduled tasks only support these unattended-safe tools: "
            + ", ".join(sorted(SAFE_SCHEDULED_TOOLS))
        )
    return tools


def _schedule_path(schedule_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", str(schedule_id or ""), re.IGNORECASE):
        raise ValueError("Invalid schedule id.")
    return schedules_dir() / f"{schedule_id}.json"


@contextmanager
def schedule_lock():
    lock_path = app_support_dir() / "schedules.lock"
    ensure_private_directory(lock_path.parent)
    with lock_path.open("a+", encoding="utf-8") as handle:
        lock_path.chmod(0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_schedule_unlocked(schedule_id: str) -> dict:
    path = _schedule_path(schedule_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Unknown schedule: {schedule_id}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Schedule file is invalid: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Schedule file is invalid: {path}")
    return value


def _write_schedule_unlocked(schedule: dict) -> None:
    directory = ensure_private_directory(schedules_dir())
    path = _schedule_path(str(schedule["id"]))
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def create_schedule(
    *,
    name: str,
    prompt: str,
    workspace: str,
    recurrence: str,
    schedule_spec: dict,
    tools=None,
    output_subdirectory: str = "",
    now: datetime | None = None,
) -> dict:
    if obsidian_vault_path() is None:
        raise ValueError("Choose an Obsidian Vault in LangBridge Settings first.")
    cleaned_name = _clean_name(name)
    cleaned_prompt = str(prompt or "").strip()
    if not cleaned_prompt:
        raise ValueError("prompt is required.")
    workspace_path = Path(workspace or os.getcwd()).expanduser().resolve()
    if not workspace_path.is_dir():
        raise ValueError(f"Workspace does not exist: {workspace_path}")
    current = now or _now()
    item = {
        "version": SCHEDULE_VERSION,
        "id": str(uuid.uuid4()),
        "name": cleaned_name,
        "prompt": cleaned_prompt,
        "workspace": str(workspace_path),
        "recurrence": recurrence,
        "schedule_spec": dict(schedule_spec or {}),
        "tools": _clean_tools(tools),
        "output_subdirectory": _clean_output_subdirectory(output_subdirectory, cleaned_name),
        "enabled": True,
        "created_at": current.isoformat(timespec="seconds"),
        "updated_at": current.isoformat(timespec="seconds"),
        "next_run_at": compute_next_run(recurrence, schedule_spec, now=current),
        "last_run_at": None,
        "last_status": "never",
        "last_error": "",
        "last_output_path": "",
        "running_pid": None,
    }
    with schedule_lock():
        _write_schedule_unlocked(item)
    return item


def get_schedule(schedule_id: str) -> dict:
    with schedule_lock():
        return _read_schedule_unlocked(schedule_id)


def list_schedules() -> list[dict]:
    directory = schedules_dir()
    if not directory.is_dir():
        return []
    items = []
    with schedule_lock():
        for path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                items.append(value)
    return sorted(items, key=lambda item: (str(item.get("name", "")).lower(), str(item.get("id", ""))))


def update_schedule(schedule_id: str, changes: dict, *, now: datetime | None = None) -> dict:
    allowed = {
        "name", "prompt", "workspace", "recurrence", "schedule_spec", "tools",
        "output_subdirectory",
    }
    unexpected = sorted(set(changes) - allowed)
    if unexpected:
        raise ValueError(f"Unsupported schedule fields: {', '.join(unexpected)}")
    current = now or _now()
    with schedule_lock():
        item = _read_schedule_unlocked(schedule_id)
        if "name" in changes:
            item["name"] = _clean_name(changes["name"])
        if "prompt" in changes:
            prompt = str(changes["prompt"] or "").strip()
            if not prompt:
                raise ValueError("prompt is required.")
            item["prompt"] = prompt
        if "workspace" in changes:
            workspace = Path(changes["workspace"]).expanduser().resolve()
            if not workspace.is_dir():
                raise ValueError(f"Workspace does not exist: {workspace}")
            item["workspace"] = str(workspace)
        if "tools" in changes:
            item["tools"] = _clean_tools(changes["tools"])
        if "output_subdirectory" in changes:
            item["output_subdirectory"] = _clean_output_subdirectory(
                changes["output_subdirectory"], item["name"]
            )
        if "recurrence" in changes:
            item["recurrence"] = str(changes["recurrence"])
        if "schedule_spec" in changes:
            item["schedule_spec"] = dict(changes["schedule_spec"] or {})
        if "recurrence" in changes or "schedule_spec" in changes:
            item["next_run_at"] = compute_next_run(
                item["recurrence"], item["schedule_spec"], now=current
            )
        item["updated_at"] = current.isoformat(timespec="seconds")
        _write_schedule_unlocked(item)
        return item


def set_schedule_enabled(schedule_id: str, enabled: bool, *, now: datetime | None = None) -> dict:
    current = now or _now()
    with schedule_lock():
        item = _read_schedule_unlocked(schedule_id)
        item["enabled"] = bool(enabled)
        if enabled and item.get("recurrence") != "once":
            item["next_run_at"] = compute_next_run(
                item["recurrence"], item["schedule_spec"], now=current
            )
        item["updated_at"] = current.isoformat(timespec="seconds")
        _write_schedule_unlocked(item)
        return item


def delete_schedule(schedule_id: str) -> dict:
    with schedule_lock():
        item = _read_schedule_unlocked(schedule_id)
        path = _schedule_path(schedule_id)
        trash = ensure_private_directory(schedules_dir() / ".trash")
        path.replace(trash / f"{schedule_id}-{int(_now().timestamp())}.json")
        return item


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_now().tzinfo)


def pid_is_running(pid) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        os.kill(value, 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
