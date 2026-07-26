"""JSON / JSONL helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def load_jsonl(path: Path | str) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path | str, rows: Iterable[dict], *, append: bool = False) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    count = 0
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def load_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path | str, data: Any, *, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")


def existing_task_ids_from_jsonl(path: Path | str) -> set[str]:
    ids: set[str] = set()
    for row in load_jsonl(path):
        tid = row.get("task_id") or row.get("instance_id")
        if tid:
            ids.add(tid)
    return ids


def dropped_task_ids_from_json(path: Path | str) -> set[str]:
    """Load ``task_id`` set from a drop.json ``{dropped: [...]}`` file.

    Also accepts legacy ``excluded`` key / ``exclude.json`` filename content.
    """
    path = Path(path)
    if not path.exists():
        # Fall back to legacy exclude.json next to drop.json.
        legacy = path.with_name("exclude.json") if path.name == "drop.json" else None
        if legacy is None or not legacy.exists():
            return set()
        path = legacy
    data = load_json(path)
    rows = data.get("dropped")
    if rows is None:
        rows = data.get("excluded") or []
    return {
        str(entry["task_id"])
        for entry in rows
        if isinstance(entry, dict) and entry.get("task_id")
    }


def load_drop_entries(path: Path | str) -> list[dict]:
    """Load drop entries; migrates legacy ``excluded`` key if present."""
    path = Path(path)
    read_path = path
    if not read_path.exists():
        legacy = path.with_name("exclude.json") if path.name == "drop.json" else None
        if legacy is None or not legacy.exists():
            return []
        read_path = legacy
    data = load_json(read_path)
    rows = data.get("dropped")
    if rows is None:
        rows = data.get("excluded") or []
    return [e for e in rows if isinstance(e, dict) and e.get("task_id")]


def save_drop_file(path: Path | str, dropped: list[dict], *, description: str) -> None:
    write_json(path, {"description": description, "dropped": dropped})
