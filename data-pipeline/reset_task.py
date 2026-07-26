#!/usr/bin/env python3
"""Erase one task from the whole pipeline — as if it was never collected.

Removes rows/files from:
  collect / env / reference jsonl + stage drop.json
  curate/out/<id>.json + curate drop.json
  data/eval/specs/<id>.json
  data/eval/docker-images/<id>/
  data/eval/drop/ (drop.json entry + archived spec/docker-images)
  local docker tag ``lb-task:<id>``

Does **not** touch eval run artifacts under ``artifacts/``.

```bash
uv run python data-pipeline/reset_task.py pytest-dev__pytest-14730 --dry-run
uv run python data-pipeline/reset_task.py pytest-dev__pytest-14730
```
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.docker_util import remove_image  # noqa: E402
from _lib.io import load_drop_entries, load_jsonl, save_drop_file, write_jsonl  # noqa: E402

_DROP_DESCRIPTIONS = {
    paths.DEFAULT_ENV_DROP: (
        "Tasks dropped at env (Dockerfile/build/smoke failure). "
        "No deletes — id stays out of env/out only."
    ),
    paths.DEFAULT_REFERENCE_DROP: (
        "Tasks dropped at reference (no FAIL_TO_PASS, patch apply failure, "
        "missing image, timeout, …)."
    ),
    paths.DEFAULT_CURATE_DROP: (
        "LLM/pipeline curate drops. Code reads this for resume. "
        "Human error-analysis drops go in data/eval/drop/ instead. "
        "Not synced to data/eval/specs/."
    ),
    paths.DEFAULT_HUMAN_DROP: (
        "HUMAN error-analysis drops. Archived under drop/specs/ and "
        "drop/docker-images/. Use drop_task.py. LLM drops go in "
        "data-pipeline/curate/out/drop.json."
    ),
}


def _row_id(row: dict) -> str:
    return str(row.get("task_id") or row.get("instance_id") or "")


def _filter_jsonl(path: Path, task_id: str, *, dry_run: bool) -> int:
    if not path.exists():
        print(f"  skip (missing): {path}")
        return 0
    rows = load_jsonl(path)
    kept = [row for row in rows if _row_id(row) != task_id]
    removed = len(rows) - len(kept)
    if removed == 0:
        print(f"  no row: {path}")
        return 0
    print(f"  {'would remove' if dry_run else 'removed'} {removed} row(s): {path}")
    if not dry_run:
        write_jsonl(path, kept)
    return removed


def _filter_drop(path: Path, task_id: str, *, dry_run: bool) -> int:
    if not path.exists():
        print(f"  skip (missing): {path}")
        return 0
    from _lib.io import load_json

    data = load_json(path)
    entries = load_drop_entries(path)
    kept = [entry for entry in entries if entry.get("task_id") != task_id]
    removed = len(entries) - len(kept)
    if removed == 0:
        print(f"  no entry: {path}")
        return 0
    print(f"  {'would remove' if dry_run else 'removed'} {removed} drop entr(y/ies): {path}")
    if not dry_run:
        description = str(
            data.get("description")
            or _DROP_DESCRIPTIONS.get(path)
            or f"Drops for {path.parent.name}."
        )
        save_drop_file(path, kept, description=description)
    return removed


def _rm_path(path: Path, *, dry_run: bool) -> bool:
    if path.is_symlink() or path.is_file():
        print(f"  {'would delete' if dry_run else 'delete'} file: {path}")
        if not dry_run:
            path.unlink()
        return True
    if path.is_dir():
        print(f"  {'would delete' if dry_run else 'delete'} dir:  {path}")
        if not dry_run:
            shutil.rmtree(path)
        return True
    print(f"  skip (missing): {path}")
    return False


def reset_task(task_id: str, *, dry_run: bool = False, keep_image_tag: bool = False) -> int:
    print(f"reset task: {task_id}" + (" (dry-run)" if dry_run else ""))

    print("\n[jsonl]")
    _filter_jsonl(paths.DEFAULT_COLLECT_JSONL, task_id, dry_run=dry_run)
    _filter_jsonl(paths.DEFAULT_ENV_JSONL, task_id, dry_run=dry_run)
    _filter_jsonl(paths.DEFAULT_REFERENCE_JSONL, task_id, dry_run=dry_run)

    print("\n[drop.json]")
    for drop_path in (
        paths.DEFAULT_ENV_DROP,
        paths.DEFAULT_REFERENCE_DROP,
        paths.DEFAULT_CURATE_DROP,
        paths.DEFAULT_HUMAN_DROP,
    ):
        _filter_drop(drop_path, task_id, dry_run=dry_run)

    print("\n[files]")
    _rm_path(paths.CURATE_OUT / f"{task_id}.json", dry_run=dry_run)
    _rm_path(paths.SPECS_DIR / f"{task_id}.json", dry_run=dry_run)
    _rm_path(paths.DOCKER_IMAGES_DIR / task_id, dry_run=dry_run)
    _rm_path(paths.HUMAN_DROP_DIR / "specs" / f"{task_id}.json", dry_run=dry_run)
    _rm_path(paths.HUMAN_DROP_DIR / "docker-images" / task_id, dry_run=dry_run)

    print("\n[docker tag]")
    tag = paths.task_image(task_id)
    if keep_image_tag:
        print(f"  keep {tag}")
    elif dry_run:
        print(f"  would docker rmi -f {tag}")
    else:
        ok = remove_image(tag)
        print(f"  docker rmi -f {tag}: {'ok' if ok else 'failed/absent'}")

    print("\nDone." + (" (dry-run, nothing written)" if dry_run else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", help="e.g. pytest-dev__pytest-14730")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be removed without writing",
    )
    parser.add_argument(
        "--keep-image-tag",
        action="store_true",
        help="do not docker rmi lb-task:<id>",
    )
    args = parser.parse_args(argv)
    return reset_task(
        args.task_id,
        dry_run=args.dry_run,
        keep_image_tag=args.keep_image_tag,
    )


if __name__ == "__main__":
    raise SystemExit(main())
