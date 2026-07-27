"""Run the interactive-bench pipeline end-to-end.

```bash
uv run python interactive-bench/data-pipeline/run_pipeline.py --data-dir /path/to/swe-chat --limit 5
uv run python interactive-bench/data-pipeline/run_pipeline.py --data-dir /path/to/swe-chat --from enrich
uv run python interactive-bench/data-pipeline/run_pipeline.py --only intent --limit 10
```

Stages: collect → resolve → enrich → intent → env → reference → curate

``env`` / ``reference`` need Docker + ``langbridge-bench:py312`` base image.
``intent`` uses heuristics if no OpenAI-compatible API key is set.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.io_util import load_json, load_jsonl  # noqa: E402

STAGES = (
    "collect",
    "resolve",
    "enrich",
    "intent",
    "env",
    "reference",
    "curate",
)


def _script(stage: str) -> Path:
    return {
        "collect": PIPELINE / "collect" / "collect.py",
        "resolve": PIPELINE / "resolve" / "resolve.py",
        "enrich": PIPELINE / "enrich" / "enrich.py",
        "intent": PIPELINE / "intent" / "analyze.py",
        "env": PIPELINE / "env" / "build_env.py",
        "reference": PIPELINE / "reference" / "reference_test.py",
        "curate": PIPELINE / "curate" / "curate.py",
    }[stage]


def _task_ids(jsonl: Path) -> set[str]:
    return {r["task_id"] for r in load_jsonl(jsonl) if r.get("task_id")}


def _drop_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        e["task_id"]
        for e in (load_json(path).get("dropped") or [])
        if isinstance(e, dict) and e.get("task_id")
    }


def pending_for(stage: str) -> set[str]:
    collect = _task_ids(paths.DEFAULT_COLLECT_JSONL)
    resolve = _task_ids(paths.DEFAULT_RESOLVE_JSONL)
    resolve_drop = _drop_ids(paths.DEFAULT_RESOLVE_DROP)
    enrich = _task_ids(paths.DEFAULT_ENRICH_JSONL)
    enrich_drop = _drop_ids(paths.DEFAULT_ENRICH_DROP)
    intent = _task_ids(paths.DEFAULT_INTENT_JSONL)
    intent_drop = _drop_ids(paths.DEFAULT_INTENT_DROP)
    env = _task_ids(paths.DEFAULT_ENV_JSONL)
    env_drop = _drop_ids(paths.DEFAULT_ENV_DROP)
    ref = _task_ids(paths.DEFAULT_REFERENCE_JSONL)
    ref_drop = _drop_ids(paths.DEFAULT_REFERENCE_DROP)
    curate = _task_ids(paths.DEFAULT_CURATE_JSONL)
    curate_drop = _drop_ids(paths.DEFAULT_CURATE_DROP)

    if stage == "resolve":
        return collect - resolve - resolve_drop
    if stage == "enrich":
        return resolve - enrich - enrich_drop
    if stage == "intent":
        return enrich - intent - intent_drop
    if stage == "env":
        return intent - env - env_drop
    if stage == "reference":
        return env - ref - ref_drop
    if stage == "curate":
        return ref - curate - curate_drop
    if stage == "collect":
        return set()
    raise ValueError(stage)


def next_stage(allowed: list[str]) -> str | None:
    for stage in ("curate", "reference", "env", "intent", "enrich", "resolve"):
        if stage in allowed and pending_for(stage):
            return stage
    if "collect" in allowed:
        return "collect"
    return None


def run_stage(
    stage: str,
    *,
    limit: int,
    data_dir: Path | None,
) -> int:
    cmd = [sys.executable, str(_script(stage))]
    if limit:
        cmd += ["--limit", str(limit)]
    if data_dir and stage in {"collect", "resolve", "enrich", "intent"}:
        cmd += ["--data-dir", str(data_dir)]
    print(f"\n=== {stage} ===\n+ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(PIPELINE.parent))


def select_stages(args: argparse.Namespace) -> list[str]:
    if args.only:
        return [args.only]
    start = STAGES.index(args.start_from)
    return list(STAGES[start:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="SWE-Chat parquet directory")
    parser.add_argument("--limit", type=int, default=0, help="Target new curated specs (full run)")
    parser.add_argument("--from", dest="start_from", choices=STAGES, default="collect")
    parser.add_argument("--only", choices=STAGES, default=None)
    parser.add_argument(
        "--max-iters",
        type=int,
        default=200,
        help="Safety cap on stage invocations",
    )
    args = parser.parse_args()

    if args.only:
        if args.only in {"collect", "resolve"} and not args.data_dir:
            print("--data-dir required for collect/resolve", file=sys.stderr)
            return 2
        return run_stage(args.only, limit=args.limit or 0, data_dir=args.data_dir)

    allowed = select_stages(args)
    if "collect" in allowed and not args.data_dir:
        print("--data-dir required when running collect", file=sys.stderr)
        return 2

    target = args.limit or 0
    before = len(_task_ids(paths.DEFAULT_CURATE_JSONL))
    for _ in range(args.max_iters):
        curated = len(_task_ids(paths.DEFAULT_CURATE_JSONL))
        if target and (curated - before) >= target:
            print(f"reached target +{curated - before} curated specs")
            return 0
        stage = next_stage(allowed)
        if stage is None:
            print("no pending work")
            return 0
        # When draining, attempt one (or small batch) at a time
        stage_limit = 1
        if stage == "collect":
            stage_limit = max(target * 3, args.limit or 5) if target else (args.limit or 20)
        rc = run_stage(stage, limit=stage_limit, data_dir=args.data_dir)
        if rc != 0:
            print(f"stage {stage} exited {rc}", file=sys.stderr)
            return rc
    print("max-iters reached", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
