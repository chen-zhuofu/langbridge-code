"""Build per-task ``lb-interactive:<id>`` Docker images.

Reads enrich output (before curate/LLM) so Docker work is not gated on LLM spend.

```bash
uv run python interactive-bench/data-pipeline/env/build_env.py --limit 1
```
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.docker_util import build_task_image, docker, render_python_dockerfile  # noqa: E402
from _lib.io_util import append_drop, load_json, load_jsonl, write_jsonl  # noqa: E402


def prepare_dockerfile(inst: dict) -> Path:
    task_id = inst["task_id"]
    tdir = paths.task_dir(task_id)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "Dockerfile").write_text(
        render_python_dockerfile(inst["repo"], inst["base_commit"]),
        encoding="utf-8",
    )
    return tdir


def smoke_test(task_id: str, timeout: int = 120) -> tuple[bool, str]:
    tag = paths.task_image(task_id)
    result = docker(
        [
            "run",
            "--rm",
            tag,
            "bash",
            "-lc",
            ".refvenv/bin/python -c 'import pytest; print(pytest.__version__)'",
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "smoke failed")[-500:]
    return True, (result.stdout or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--in", dest="inp", type=Path, default=paths.DEFAULT_ENRICH_JSONL)
    parser.add_argument("--out", type=Path, default=paths.DEFAULT_ENV_JSONL)
    parser.add_argument("--drop", type=Path, default=paths.DEFAULT_ENV_DROP)
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()

    done = {r["task_id"] for r in load_jsonl(args.out) if r.get("task_id")}
    if args.drop.exists():
        for entry in load_json(args.drop).get("dropped") or []:
            if isinstance(entry, dict) and entry.get("task_id"):
                done.add(entry["task_id"])

    kept: list[dict] = []
    for inst in load_jsonl(args.inp):
        if args.limit and len(kept) >= args.limit:
            break
        tid = inst.get("task_id")
        if not tid or tid in done:
            continue
        try:
            prepare_dockerfile(inst)
            tag = build_task_image(tid, base_commit=str(inst.get("base_commit") or ""))
            if not args.skip_smoke:
                ok, detail = smoke_test(tid)
                if not ok:
                    append_drop(args.drop, tid, f"smoke failed: {detail}")
                    done.add(tid)
                    print(f"  drop {tid}: smoke failed")
                    continue
            out = dict(inst)
            out["docker_image"] = tag
            kept.append(out)
            done.add(tid)
            print(f"  ok {tid}: {tag}")
        except Exception as exc:  # noqa: BLE001
            append_drop(args.drop, tid, f"error: {exc}")
            done.add(tid)
            print(f"  drop {tid}: error: {exc}")

    existing = {r["task_id"]: r for r in load_jsonl(args.out) if r.get("task_id")}
    for r in kept:
        existing[r["task_id"]] = r
    write_jsonl(args.out, existing.values(), append=False)
    print(f"env {len(kept)} new; total {len(existing)}; out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
