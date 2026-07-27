"""Session-artifact plan file (todo_list.md) helpers."""
import re
from pathlib import Path

from langbridge_code.agents.common.workspace import get_workspace_root

PLAN_FILENAME = "todo_list.md"

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def plan_path():
    return get_workspace_root() / PLAN_FILENAME


def read_plan_file(run_log_path=None) -> str:
    path = artifact_plan_path(run_log_path) or plan_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def artifact_plan_path(run_log_path) -> Path | None:
    if run_log_path is None:
        return None
    artifact_directory = Path(run_log_path)
    # Current sessions pass their artifact directory. Legacy harnesses may
    # still pass a run.json file and keep workspace-local behavior.
    if artifact_directory.suffix:
        return None
    return artifact_directory / PLAN_FILENAME


def migrate_workspace_plan(run_log_path) -> Path | None:
    """Move one legacy workspace plan into artifacts, leaving no root copy."""
    destination = artifact_plan_path(run_log_path)
    if destination is None:
        return None
    source = plan_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        if not destination.exists():
            temporary = destination.with_suffix(".md.tmp")
            temporary.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            temporary.replace(destination)
        source.unlink()
    return destination


def clean_task_text(text: str) -> str:
    stripped = _HTML_COMMENT.sub("", text or "").strip()
    return " ".join(stripped.split())
