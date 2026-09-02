"""One main-agent tool for creating and managing unattended schedules."""
from __future__ import annotations

import json
import os

from langbridge_code.schedules import (
    SAFE_SCHEDULED_TOOLS,
    create_schedule,
    delete_schedule,
    list_schedules,
    set_schedule_enabled,
    update_schedule,
)
from langbridge_code.tools.common.description import DESCRIPTION_PARAMETER


def _spec(*, recurrence: str, time: str | None, weekday: int | None, run_at: str | None, cron: str | None) -> dict:
    if recurrence == "once":
        return {"run_at": run_at}
    if recurrence == "cron":
        return {"cron": cron}
    result = {"time": time}
    if recurrence == "weekly":
        result["weekday"] = weekday
    return result


def schedule(
    action: str,
    schedule_id: str | None = None,
    name: str | None = None,
    prompt: str | None = None,
    workspace: str | None = None,
    recurrence: str | None = None,
    time: str | None = None,
    weekday: int | None = None,
    run_at: str | None = None,
    cron: str | None = None,
    tools: list[str] | None = None,
    output_subdirectory: str | None = None,
) -> str:
    selected = str(action or "").strip().lower()
    if selected == "list":
        result = {"schedules": list_schedules()}
    elif selected == "create":
        if not recurrence:
            raise ValueError("recurrence is required for action='create'.")
        result = create_schedule(
            name=name or "",
            prompt=prompt or "",
            workspace=workspace or os.getcwd(),
            recurrence=recurrence,
            schedule_spec=_spec(
                recurrence=recurrence, time=time, weekday=weekday, run_at=run_at, cron=cron
            ),
            tools=tools,
            output_subdirectory=output_subdirectory or "",
        )
        from langbridge_code.scheduler import ensure_launch_agent

        ensure_launch_agent()
    elif selected == "update":
        if not schedule_id:
            raise ValueError("schedule_id is required for action='update'.")
        changes = {
            key: value
            for key, value in {
                "name": name,
                "prompt": prompt,
                "workspace": workspace,
                "tools": tools,
                "output_subdirectory": output_subdirectory,
            }.items()
            if value is not None
        }
        if recurrence is not None:
            changes["recurrence"] = recurrence
            changes["schedule_spec"] = _spec(
                recurrence=recurrence, time=time, weekday=weekday, run_at=run_at, cron=cron
            )
        result = update_schedule(schedule_id, changes)
    elif selected in {"pause", "resume"}:
        if not schedule_id:
            raise ValueError(f"schedule_id is required for action='{selected}'.")
        result = set_schedule_enabled(schedule_id, selected == "resume")
    elif selected == "run_now":
        if not schedule_id:
            raise ValueError("schedule_id is required for action='run_now'.")
        from langbridge_code.scheduler import launch_schedule

        result = launch_schedule(schedule_id, advance=False)
    elif selected == "delete":
        if not schedule_id:
            raise ValueError("schedule_id is required for action='delete'.")
        result = {"deleted": delete_schedule(schedule_id)}
    else:
        raise ValueError(
            "action must be one of: create, list, update, pause, resume, run_now, delete."
        )
    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "schedule",
        "description": (
            "Create and manage confirmed unattended macOS schedules. Use one action per call. "
            "Creating, materially updating, or deleting a schedule requires user approval. "
            "Scheduled runs use only the explicitly listed read-only tools and write their final "
            "report beneath the Obsidian Vault selected in LangBridge Settings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": DESCRIPTION_PARAMETER,
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "update", "pause", "resume", "run_now", "delete"],
                },
                "schedule_id": {"type": "string"},
                "name": {"type": "string"},
                "prompt": {"type": "string"},
                "workspace": {"type": "string"},
                "recurrence": {
                    "type": "string",
                    "enum": ["once", "daily", "weekdays", "weekly", "cron"],
                },
                "time": {
                    "type": "string",
                    "description": "Local 24-hour HH:MM time for daily, weekdays, or weekly.",
                },
                "weekday": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 6,
                    "description": "Weekly day: Monday=0 through Sunday=6.",
                },
                "run_at": {
                    "type": "string",
                    "description": "Local ISO date-time for a one-time schedule.",
                },
                "cron": {"type": "string", "description": "Five-field cron expression."},
                "tools": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(SAFE_SCHEDULED_TOOLS)},
                },
                "output_subdirectory": {
                    "type": "string",
                    "description": "Relative directory inside the selected Obsidian Vault.",
                },
            },
            "required": ["description", "action"],
            "additionalProperties": False,
        },
    }
]

TOOLS = {"schedule": schedule}
