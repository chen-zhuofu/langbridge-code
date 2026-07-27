"""JSON / JSONL helpers for the interactive pipeline."""
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
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path | str, data: Any, *, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")


def append_drop(path: Path | str, task_id: str, reason: str) -> None:
    path = Path(path)
    data = load_json(path) if path.exists() else {"dropped": []}
    rows = data.get("dropped")
    if not isinstance(rows, list):
        rows = []
    # replace existing entry for same id
    rows = [r for r in rows if not (isinstance(r, dict) and r.get("task_id") == task_id)]
    rows.append({"task_id": task_id, "reason": reason})
    write_json(path, {"dropped": rows})
