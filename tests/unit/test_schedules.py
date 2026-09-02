import json
import os
from datetime import datetime, timezone

import pytest

from langbridge_code.agents.common.approval import approval_reason
from langbridge_code.schedules import (
    compute_next_run,
    create_schedule,
    delete_schedule,
    list_schedules,
    set_schedule_enabled,
    update_schedule,
)
from langbridge_code.tools import GOAL_VERIFICATION_TOOL_NAMES, MAIN_TOOL_NAMES
from langbridge_code.tools.schedule import schedule
from langbridge_code.scheduler import _write_obsidian_report, launch_schedule
from langbridge_code.schedules import _write_schedule_unlocked, schedule_lock


@pytest.fixture
def schedule_home(tmp_path, monkeypatch):
    support = tmp_path / "Application Support" / "LangBridge"
    vault = tmp_path / "Obsidian"
    vault.mkdir()
    support.mkdir(parents=True)
    (support / "preferences.json").write_text(
        json.dumps({"obsidian_vault_path": str(vault)}), encoding="utf-8"
    )
    monkeypatch.setenv("LANGBRIDGE_APP_SUPPORT_DIR", str(support))
    monkeypatch.setenv("LANGBRIDGE_DISABLE_LAUNCH_AGENT", "1")
    return support, vault


def test_compute_next_run_for_daily_weekday_and_cron():
    now = datetime(2026, 8, 28, 22, 0, tzinfo=timezone.utc)  # Friday
    assert compute_next_run("daily", {"time": "21:00"}, now=now).startswith(
        "2026-08-29T21:00:00"
    )
    assert compute_next_run("weekdays", {"time": "09:00"}, now=now).startswith(
        "2026-08-31T09:00:00"
    )
    assert compute_next_run("cron", {"cron": "0 */6 * * *"}, now=now).startswith(
        "2026-08-29T00:00:00"
    )


def test_schedule_crud_is_persistent_and_recoverable(schedule_home, tmp_path):
    item = create_schedule(
        name="X Daily",
        prompt="Summarize interesting posts.",
        workspace=str(tmp_path),
        recurrence="daily",
        schedule_spec={"time": "21:00"},
        tools=["browser", "browser", "read_skill"],
    )
    assert item["tools"] == ["browser", "read_skill"]
    assert item["output_subdirectory"] == "LangBridge/X Daily"
    assert list_schedules()[0]["id"] == item["id"]

    updated = update_schedule(item["id"], {"prompt": "Summarize the best posts."})
    assert updated["prompt"] == "Summarize the best posts."
    assert set_schedule_enabled(item["id"], False)["enabled"] is False
    assert set_schedule_enabled(item["id"], True)["enabled"] is True

    delete_schedule(item["id"])
    assert list_schedules() == []
    assert list((schedule_home[0] / "schedules" / ".trash").glob("*.json"))


def test_schedule_can_explicitly_allow_read_only_gmail(schedule_home, tmp_path):
    item = create_schedule(
        name="Gmail Daily",
        prompt="Summarize important messages.",
        workspace=str(tmp_path),
        recurrence="daily",
        schedule_spec={"time": "21:00"},
        tools=["gmail"],
    )
    assert item["tools"] == ["gmail"]


def test_schedule_tool_requires_vault_and_limits_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_APP_SUPPORT_DIR", str(tmp_path / "support"))
    monkeypatch.setenv("LANGBRIDGE_DISABLE_LAUNCH_AGENT", "1")
    with pytest.raises(ValueError, match="Obsidian Vault"):
        schedule(
            action="create",
            name="Report",
            prompt="Write report",
            workspace=str(tmp_path),
            recurrence="daily",
            time="09:00",
        )

    vault = tmp_path / "vault"
    vault.mkdir()
    support = tmp_path / "support"
    support.mkdir()
    (support / "preferences.json").write_text(
        json.dumps({"obsidian_vault_path": str(vault)}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unattended-safe"):
        schedule(
            action="create",
            name="Unsafe",
            prompt="Change code",
            workspace=str(tmp_path),
            recurrence="daily",
            time="09:00",
            tools=["bash"],
        )


def test_schedule_is_main_only_and_mutations_require_approval():
    assert "schedule" in MAIN_TOOL_NAMES
    assert "schedule" not in GOAL_VERIFICATION_TOOL_NAMES
    assert approval_reason("schedule", {"action": "create"})
    assert approval_reason("schedule", {"action": "update"})
    assert approval_reason("schedule", {"action": "delete"})
    assert approval_reason("schedule", {"action": "list"}) is None


def test_obsidian_report_appends_runs_to_daily_note(schedule_home, tmp_path):
    item = create_schedule(
        name="Daily Research",
        prompt="Summarize research.",
        workspace=str(tmp_path),
        recurrence="daily",
        schedule_spec={"time": "21:00"},
    )
    started = datetime(2026, 8, 26, 21, 0, tzinfo=timezone.utc)
    path = _write_obsidian_report(item, "First result", started)
    _write_obsidian_report(item, "Second result", started.replace(hour=22))
    content = path.read_text(encoding="utf-8")
    assert "# Daily Research — 2026-08-26" in content
    assert "## 21:00\n\nFirst result" in content
    assert "## 22:00\n\nSecond result" in content
    assert path.is_relative_to(schedule_home[1])


def test_same_schedule_does_not_overlap(schedule_home, tmp_path):
    item = create_schedule(
        name="No overlap",
        prompt="Read only.",
        workspace=str(tmp_path),
        recurrence="daily",
        schedule_spec={"time": "21:00"},
    )
    item["running_pid"] = os.getpid()
    with schedule_lock():
        _write_schedule_unlocked(item)
    result = launch_schedule(item["id"], advance=False)
    assert result["status"] == "already-running"
