"""Stage 2 — build per-task Docker env (Dockerfile + image tag).

Input: collect ``out/instances.jsonl``.
Resume: skip if already in **this** stage's ``out/`` or ``drop.json``.

On success → ``env/out/instances.jsonl``.
On failure → append ``env/out/drop.json`` only (no deletes).

Does **not** write ``data/eval/specs/`` (curate owns that).

```bash
uv run python data-pipeline/env/build_env.py
uv run python data-pipeline/env/build_env.py --limit 1
```
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PIPELINE = Path(__file__).resolve().parents[1]
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from _lib import paths  # noqa: E402
from _lib.docker_util import build_task_image, docker, render_python_dockerfile  # noqa: E402
from _lib.io import load_drop_entries, load_jsonl, save_drop_file, write_jsonl  # noqa: E402


def _task_id(inst: dict) -> str:
    return str(inst.get("task_id") or inst["instance_id"])


def smoke_test(task_id: str, timeout: int = 120) -> tuple[bool, str]:
    tag = paths.task_image(task_id)
    result = docker(
        [
            "run",
            "--rm",
            tag,
            "bash",
            "-lc",
            (
                ".refvenv/bin/python -c 'import pytest; print(pytest.__version__)' "
                "&& test -x .refvenv/bin/ruff "
                "&& test -x .refvenv/bin/mypy "
                "&& test -x .refvenv/bin/bandit"
            ),
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "smoke failed")[-500:]
    return True, (result.stdout or "").strip()


def prepare_dockerfile(inst: dict) -> Path:
    """Write docker-images/<id>/Dockerfile only (no specs/)."""
    task_id = _task_id(inst)
    tdir = paths.task_dir(task_id)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "Dockerfile").write_text(
        render_python_dockerfile(inst["repo"], inst["base_commit"]),
        encoding="utf-8",
    )
    return tdir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="stop after this many new attempts (prior out/drop skips do not count)",
    )
    args = parser.parse_args()

    instances_path = paths.DEFAULT_COLLECT_JSONL
    out_path = paths.DEFAULT_ENV_JSONL
    drop_path = paths.DEFAULT_ENV_DROP

    instances = load_jsonl(instances_path)

    kept_by_id: dict[str, dict] = {}
    if out_path.exists():
        for row in load_jsonl(out_path):
            kept_by_id[_task_id(row)] = row

    dropped = load_drop_entries(drop_path)
    drop_ids = {e["task_id"] for e in dropped}

    built = 0
    attempted = 0
    skipped_kept = 0
    skipped_drop = 0

    for index, inst in enumerate(instances, start=1):
        task_id = _task_id(inst)
        print(f"\n[{index}/{len(instances)}] {task_id}")

        if task_id in drop_ids:
            print("  skip (already in env drop.json)")
            skipped_drop += 1
            kept_by_id.pop(task_id, None)
            continue

        if task_id in kept_by_id:
            print("  skip (already in env out)")
            skipped_kept += 1
            continue

        attempted += 1
        try:
            prepare_dockerfile(inst)
            built_tag = build_task_image(task_id)
            print(f"  built {built_tag}")
            ok, detail = smoke_test(task_id)
            if not ok:
                raise RuntimeError(f"smoke failed: {detail}")
            print(f"  smoke ok ({detail})")
            kept_by_id[task_id] = inst
            built += 1
        except Exception as error:  # noqa: BLE001
            reason = str(error)[:500]
            print(f"  DROP: {reason}")
            dropped.append({"task_id": task_id, "reason": reason, "stage": "env"})
            drop_ids.add(task_id)
            kept_by_id.pop(task_id, None)

        if args.limit and attempted >= args.limit:
            print(f"\n[limit] reached {args.limit} new attempt(s); stopping.")
            break

    kept = list(kept_by_id.values())
    write_jsonl(out_path, kept)
    save_drop_file(
        drop_path,
        dropped,
        description="Tasks dropped because the Docker env could not execute the test suite.",
    )
    print(
        f"\nKept {len(kept)} (attempted {attempted}, built {built}, "
        f"skip-kept {skipped_kept}, skip-drop {skipped_drop}) -> {out_path}"
    )
    print(f"Dropped {len(dropped)} -> {drop_path}")
    print("Next: data-pipeline/reference/reference_test.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
