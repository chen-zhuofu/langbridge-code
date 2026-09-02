"""macOS LaunchAgent host and one-shot runner for LangBridge schedules."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from langbridge_code.schedules import (
    _now,
    _read_schedule_unlocked,
    _write_schedule_unlocked,
    compute_next_run,
    create_schedule,
    delete_schedule,
    get_schedule,
    ensure_private_directory,
    list_schedules,
    obsidian_vault_path,
    parse_timestamp,
    pid_is_running,
    schedule_lock,
    schedule_logs_dir,
    schedule_runs_dir,
    set_schedule_enabled,
    update_schedule,
)

LAUNCH_AGENT_LABEL = "com.langbridge.app.scheduler"


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def _launch_agent_payload() -> dict:
    logs = ensure_private_directory(schedule_logs_dir())
    root = repository_root()
    source = root / "src"
    python_paths = [str(source)]
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if entry and entry not in python_paths:
            python_paths.append(entry)
    environment = {
        "PYTHONPATH": os.pathsep.join(python_paths),
    }
    notifier = os.environ.get("LANGBRIDGE_NOTIFIER_PATH", "").strip()
    if notifier:
        environment["LANGBRIDGE_NOTIFIER_PATH"] = notifier
    credential_helper = os.environ.get("LANGBRIDGE_CREDENTIAL_HELPER", "").strip()
    if credential_helper:
        environment["LANGBRIDGE_CREDENTIAL_HELPER"] = credential_helper
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [sys.executable, "-m", "langbridge_code.scheduler", "tick"],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "StartInterval": 60,
        "ProcessType": "Background",
        "Umask": 0o077,
        "StandardOutPath": str(logs / "scheduler.stdout.log"),
        "StandardErrorPath": str(logs / "scheduler.stderr.log"),
    }


def ensure_launch_agent() -> dict:
    """Install or refresh this user's LaunchAgent without requiring root."""
    if os.environ.get("LANGBRIDGE_DISABLE_LAUNCH_AGENT") == "1":
        return {"status": "disabled-for-tests"}
    if sys.platform != "darwin":
        raise RuntimeError("Schedules are supported only on macOS.")
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = plistlib.dumps(_launch_agent_payload(), fmt=plistlib.FMT_XML, sort_keys=True)
    changed = not path.exists() or path.read_bytes() != encoded
    if changed:
        temporary = path.with_suffix(".plist.tmp")
        temporary.write_bytes(encoded)
        temporary.chmod(0o600)
        temporary.replace(path)
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LAUNCH_AGENT_LABEL}"
    loaded = subprocess.run(
        ["launchctl", "print", target], capture_output=True, check=False
    ).returncode == 0
    if changed and loaded:
        subprocess.run(
            ["launchctl", "bootout", domain, str(path)], capture_output=True, check=False
        )
        loaded = False
    if not loaded:
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "launchctl bootstrap failed").strip())
    return {"status": "installed", "path": str(path), "changed": changed}


def launch_schedule(schedule_id: str, *, advance: bool) -> dict:
    """Claim and start one schedule while holding the cross-process lock."""
    now = _now()
    logs = ensure_private_directory(schedule_logs_dir())
    with schedule_lock():
        item = _read_schedule_unlocked(schedule_id)
        if pid_is_running(item.get("running_pid")):
            return {"status": "already-running", "schedule": item}
        item["running_pid"] = None
        stdout = (logs / f"{schedule_id}.stdout.log").open("a", encoding="utf-8")
        stderr = (logs / f"{schedule_id}.stderr.log").open("a", encoding="utf-8")
        (logs / f"{schedule_id}.stdout.log").chmod(0o600)
        (logs / f"{schedule_id}.stderr.log").chmod(0o600)
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "langbridge_code.scheduler", "run-task", schedule_id],
                cwd=item["workspace"],
                env=dict(os.environ),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        finally:
            stdout.close()
            stderr.close()
        item["running_pid"] = process.pid
        item["last_run_at"] = now.isoformat(timespec="seconds")
        item["last_status"] = "running"
        item["last_error"] = ""
        if advance:
            if item["recurrence"] == "once":
                item["enabled"] = False
                item["next_run_at"] = None
            else:
                item["next_run_at"] = compute_next_run(
                    item["recurrence"], item["schedule_spec"], now=now
                )
        _write_schedule_unlocked(item)
    return {"status": "started", "pid": process.pid, "schedule": item}


def run_due() -> list[dict]:
    now = _now()
    started = []
    for item in list_schedules():
        if not item.get("enabled"):
            continue
        due = parse_timestamp(item.get("next_run_at"))
        if due is not None and due <= now:
            started.append(launch_schedule(str(item["id"]), advance=True))
    return started


def _safe_output_directory(vault: Path, relative: str) -> Path:
    output = (vault / relative).resolve()
    try:
        output.relative_to(vault.resolve())
    except ValueError as error:
        raise PermissionError("Schedule output escaped the selected Obsidian Vault.") from error
    return output


def _write_obsidian_report(item: dict, reply: str, started: datetime) -> Path:
    vault = obsidian_vault_path()
    if vault is None:
        raise RuntimeError("The selected Obsidian Vault is unavailable.")
    directory = _safe_output_directory(vault, str(item["output_subdirectory"]))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{started.date().isoformat()}.md"
    section = f"## {started.strftime('%H:%M')}\n\n{reply.strip() or '_No report returned._'}"
    if path.exists():
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n\n---\n\n{section}\n")
    else:
        path.write_text(
            "---\n"
            f"langbridge_schedule: {item['id']}\n"
            f"task: {json.dumps(item['name'], ensure_ascii=False)}\n"
            f"date: {started.date().isoformat()}\n"
            "---\n\n"
            f"# {item['name']} — {started.date().isoformat()}\n\n"
            f"{section}\n",
            encoding="utf-8",
        )
    return path


def _write_run_record(
    item: dict,
    *,
    started: datetime,
    finished: datetime,
    status: str,
    output_path: str = "",
    error: str = "",
) -> Path:
    directory = ensure_private_directory(schedule_runs_dir() / str(item["id"]))
    path = directory / f"{started.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.json"
    path.write_text(
        json.dumps(
            {
                "id": path.stem,
                "schedule_id": item["id"],
                "name": item["name"],
                "started_at": started.isoformat(timespec="seconds"),
                "finished_at": finished.isoformat(timespec="seconds"),
                "status": status,
                "output_path": output_path,
                "error": error,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def list_runs(schedule_id: str) -> list[dict]:
    directory = schedule_runs_dir() / schedule_id
    if not directory.is_dir():
        return []
    results = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            results.append(value)
    return results


def _notify(title: str, body: str, output_path: str = "") -> None:
    if sys.platform != "darwin" or os.environ.get("LANGBRIDGE_DISABLE_NOTIFICATIONS") == "1":
        return
    notifier = Path(os.environ.get("LANGBRIDGE_NOTIFIER_PATH", "")).expanduser()
    if notifier.is_file() and os.access(notifier, os.X_OK):
        completed = subprocess.run(
            [str(notifier), title, body, output_path], capture_output=True, check=False
        )
        if completed.returncode == 0:
            return
    script = "display notification " + json.dumps(body) + " with title " + json.dumps(title)
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


def _finish_schedule(schedule_id: str, *, status: str, output_path: str, error: str) -> None:
    with schedule_lock():
        item = _read_schedule_unlocked(schedule_id)
        item["running_pid"] = None
        item["last_status"] = status
        item["last_error"] = error
        item["last_output_path"] = output_path
        item["updated_at"] = _now().isoformat(timespec="seconds")
        _write_schedule_unlocked(item)


def run_task(schedule_id: str) -> int:
    started = _now()
    item = get_schedule(schedule_id)
    try:
        workspace = Path(item["workspace"]).resolve()
        if not workspace.is_dir():
            raise RuntimeError(f"Workspace is unavailable: {workspace}")
        os.chdir(workspace)
        from langbridge_code import settings
        from langbridge_code.agents.main_agent import run_agent_turn
        from langbridge_code.settings import load_api_key
        from langbridge_code.tools.common.runtime import bootstrap_runtime
        from langbridge_code.util.session import create_run_log_path

        bootstrap_runtime()
        api_key = load_api_key()
        model = os.environ.get("LANGBRIDGE_MODEL") or settings.DEFAULT_MODEL
        prompt = (
            "[UNATTENDED_SCHEDULE]\n"
            "This task was explicitly approved for unattended execution. Do not ask the user "
            "questions and do not attempt tools outside the approved list. Return a complete "
            "standalone report suitable for an Obsidian note.\n\n"
            + str(item["prompt"])
        )
        reply = run_agent_turn(
            api_key,
            model,
            prompt,
            create_run_log_path(f"Scheduled: {item['name']}"),
            turn_id=1,
            print_reply=False,
            approval_callback=lambda *_: False,
            question_callback=lambda *_: "",
            allowed_tool_names=set(item.get("tools") or []),
        )
        output = _write_obsidian_report(item, reply, started)
        finished = _now()
        _write_run_record(
            item, started=started, finished=finished, status="completed", output_path=str(output)
        )
        _finish_schedule(schedule_id, status="completed", output_path=str(output), error="")
        _notify(
            f"LangBridge · {item['name']}", f"Completed — {output.name}", str(output)
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - persisted for unattended diagnosis
        finished = _now()
        detail = f"{exc}\n{traceback.format_exc()}"
        _write_run_record(item, started=started, finished=finished, status="failed", error=detail)
        _finish_schedule(schedule_id, status="failed", output_path="", error=str(exc))
        _notify(f"LangBridge · {item['name']}", f"Failed — {exc}")
        return 1


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="langbridge-scheduler")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "tick", "list"):
        subparsers.add_parser(command)
    for command in ("run-task", "run-now", "pause", "resume", "delete", "runs"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("schedule_id")
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--name", required=True)
    create_parser.add_argument("--prompt", required=True)
    create_parser.add_argument("--workspace", required=True)
    create_parser.add_argument("--recurrence", required=True)
    create_parser.add_argument("--schedule-spec", required=True)
    create_parser.add_argument("--tools", default="[]")
    create_parser.add_argument("--output-subdirectory", default="")
    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("schedule_id")
    update_parser.add_argument("--name", required=True)
    update_parser.add_argument("--prompt", required=True)
    update_parser.add_argument("--workspace", required=True)
    update_parser.add_argument("--recurrence", required=True)
    update_parser.add_argument("--schedule-spec", required=True)
    update_parser.add_argument("--tools", default="[]")
    update_parser.add_argument("--output-subdirectory", required=True)
    arguments = parser.parse_args(argv)

    if arguments.command == "install":
        _emit(ensure_launch_agent())
    elif arguments.command == "tick":
        _emit({"started": run_due()})
    elif arguments.command == "list":
        _emit({"schedules": list_schedules()})
    elif arguments.command == "create":
        item = create_schedule(
            name=arguments.name,
            prompt=arguments.prompt,
            workspace=arguments.workspace,
            recurrence=arguments.recurrence,
            schedule_spec=json.loads(arguments.schedule_spec),
            tools=json.loads(arguments.tools),
            output_subdirectory=arguments.output_subdirectory,
        )
        ensure_launch_agent()
        _emit(item)
    elif arguments.command == "update":
        _emit(
            update_schedule(
                arguments.schedule_id,
                {
                    "name": arguments.name,
                    "prompt": arguments.prompt,
                    "workspace": arguments.workspace,
                    "recurrence": arguments.recurrence,
                    "schedule_spec": json.loads(arguments.schedule_spec),
                    "tools": json.loads(arguments.tools),
                    "output_subdirectory": arguments.output_subdirectory,
                },
            )
        )
    elif arguments.command == "run-task":
        return run_task(arguments.schedule_id)
    elif arguments.command == "run-now":
        _emit(launch_schedule(arguments.schedule_id, advance=False))
    elif arguments.command == "pause":
        _emit(set_schedule_enabled(arguments.schedule_id, False))
    elif arguments.command == "resume":
        _emit(set_schedule_enabled(arguments.schedule_id, True))
    elif arguments.command == "delete":
        _emit({"deleted": delete_schedule(arguments.schedule_id)})
    elif arguments.command == "runs":
        _emit({"runs": list_runs(arguments.schedule_id)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
