#!/usr/bin/env python3
"""Human error-analysis drop: record reason and archive spec + docker-images.

```bash
uv run python data/langbridge-bench/drop/drop_task.py pytest-dev__pytest-14694 \\
  --reason "problem statement too vague to reproduce the hidden-test case"

# dry-run
uv run python data/langbridge-bench/drop/drop_task.py <task_id> --reason "..." --dry-run
```

Writes ``data/langbridge-bench/drop/drop.json`` via pipeline ``save_drop_file``, moves:
  ``data/langbridge-bench/specs/<id>.json`` → ``data/langbridge-bench/drop/specs/<id>.json``
  ``data/langbridge-bench/docker-images/<id>/`` → ``data/langbridge-bench/drop/docker-images/<id>/``

Optionally removes local ``lb-task:<id>`` (default on).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DROP_DIR = Path(__file__).resolve().parent
EVAL_DIR = DROP_DIR.parent
REPO_ROOT = EVAL_DIR.parents[1]
PIPELINE_LIB = REPO_ROOT / "data-pipeline"
if str(PIPELINE_LIB) not in sys.path:
    sys.path.insert(0, str(PIPELINE_LIB))

from _lib.io import load_drop_entries, save_drop_file  # noqa: E402

SPECS_DIR = EVAL_DIR / "specs"
DOCKER_IMAGES_DIR = EVAL_DIR / "docker-images"
DROP_JSON = DROP_DIR / "drop.json"
DROP_SPECS = DROP_DIR / "specs"
DROP_DOCKER = DROP_DIR / "docker-images"

_HUMAN_DROP_DESCRIPTION = (
    "HUMAN error-analysis drops. Archived under drop/specs/ and drop/docker-images/. "
    "Use drop_task.py. LLM drops go in data-pipeline/curate/out/drop.json."
)


def _rmi(task_id: str) -> None:
    tag = f"lb-task:{task_id}"
    subprocess.run(
        ["docker", "rmi", "-f", tag],
        capture_output=True,
        text=True,
        check=False,
    )


def drop_task(
    task_id: str,
    *,
    reason: str,
    source: str = "human_error_analysis",
    dry_run: bool = False,
    keep_image_tag: bool = False,
) -> int:
    src_spec = SPECS_DIR / f"{task_id}.json"
    src_docker = DOCKER_IMAGES_DIR / task_id
    dest_spec = DROP_SPECS / f"{task_id}.json"
    dest_docker = DROP_DOCKER / task_id

    dropped = load_drop_entries(DROP_JSON)
    existing = next((e for e in dropped if e.get("task_id") == task_id), None)

    print(f"task_id: {task_id}")
    print(f"  spec:   {src_spec} ({'exists' if src_spec.exists() else 'MISSING'})")
    print(f"  docker: {src_docker} ({'exists' if src_docker.is_dir() else 'MISSING'})")
    print(f"  reason: {reason}")

    if not src_spec.exists() and not src_docker.is_dir() and existing:
        print("  already archived and listed in drop.json; nothing to do")
        return 0

    if dry_run:
        print("  dry-run: would update drop.json and move artifacts")
        return 0

    DROP_SPECS.mkdir(parents=True, exist_ok=True)
    DROP_DOCKER.mkdir(parents=True, exist_ok=True)

    if src_spec.exists() or src_spec.is_symlink():
        if dest_spec.exists() or dest_spec.is_symlink():
            dest_spec.unlink()
        shutil.move(str(src_spec), str(dest_spec))
        print(f"  moved spec → {dest_spec}")
    else:
        print("  warn: no active spec to move")

    if src_docker.is_dir():
        if dest_docker.exists():
            shutil.rmtree(dest_docker)
        shutil.move(str(src_docker), str(dest_docker))
        print(f"  moved docker-images → {dest_docker}")
    else:
        print("  warn: no docker-images dir to move")

    if not keep_image_tag:
        _rmi(task_id)
        print(f"  docker rmi lb-task:{task_id} (best-effort)")

    entry = {
        "task_id": task_id,
        "reason": reason,
        "stage": "human",
        "source": source,
        "dropped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if existing:
        dropped = [e for e in dropped if e.get("task_id") != task_id]
    dropped.append(entry)
    save_drop_file(DROP_JSON, dropped, description=_HUMAN_DROP_DESCRIPTION)
    print(f"  wrote {DROP_JSON}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", help="e.g. pytest-dev__pytest-14694")
    parser.add_argument("--reason", required=True, help="why this task is dropped")
    parser.add_argument(
        "--source",
        default="human_error_analysis",
        help="source label in drop.json (default: human_error_analysis)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--keep-image-tag",
        action="store_true",
        help="do not docker rmi lb-task:<id>",
    )
    args = parser.parse_args(argv)
    return drop_task(
        args.task_id,
        reason=args.reason,
        source=args.source,
        dry_run=args.dry_run,
        keep_image_tag=args.keep_image_tag,
    )


if __name__ == "__main__":
    raise SystemExit(main())
