"""Run the interactive-bench pipeline end-to-end.

```bash
uv run python interactive-bench/data-pipeline/run_pipeline.py --data-dir /path/to/swe-chat --limit 5
uv run python interactive-bench/data-pipeline/run_pipeline.py --data-dir /path/to/swe-chat --from enrich
uv run python interactive-bench/data-pipeline/run_pipeline.py --only curate --limit 10
```

Stages: collect → resolve → enrich → env → reference → curate

``env`` / ``reference`` need Docker + ``langbridge-bench:py312`` base image.
``curate`` runs intent LLM after reference so LLM cost is only spent on tasks
that already have valid F2P. Requires a LangBridge config ``api_keys`` or env
provider key (``OPENAI_API_KEY`` / …); no heuristic fallback.

When ``--limit`` is set, collect is repo-balanced: the target is spread as
evenly as possible across the repos present in ``--data-dir`` (e.g. 20 across
9 repos -> 2 repos get 3, the rest get 2), and each collect call only tops up
repos that have fallen behind their fair share of curate.jsonl + in-flight
tasks. Repos that run out of eligible sessions give up their unused share to
repos that still have some, so the final curated count still hits the target
whenever there's enough capacity across the whole repo set. See
``repo_progress`` / ``run_collect_balanced`` / ``_lib/repo_balance.py``.
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
from _lib.io_util import load_json, load_jsonl, write_json  # noqa: E402

STAGES = (
    "collect",
    "resolve",
    "enrich",
    "env",
    "reference",
    "curate",
)


def _script(stage: str) -> Path:
    return {
        "collect": PIPELINE / "collect" / "collect.py",
        "resolve": PIPELINE / "resolve" / "resolve.py",
        "enrich": PIPELINE / "enrich" / "enrich.py",
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
    if stage == "env":
        return enrich - env - env_drop
    if stage == "reference":
        return env - ref - ref_drop
    if stage == "curate":
        return ref - curate - curate_drop
    if stage == "collect":
        return set()
    raise ValueError(stage)


def _repo_counts(jsonl: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in load_jsonl(jsonl):
        repo = row.get("repo")
        if repo:
            counts[repo] = counts.get(repo, 0) + 1
    return counts


def repo_progress() -> dict[str, dict[str, int]]:
    """Per-repo {done, pending} counts across the whole pipeline so far.

    ``done`` is repos already in curate.jsonl (final success). ``pending``
    is repos collected but still moving through resolve/enrich/.../curate
    — not yet curated, and not dropped at any stage — i.e. still "in flight"
    and could still land in curate.
    """
    curate_ids = _task_ids(paths.DEFAULT_CURATE_JSONL)
    dropped_ids: set[str] = set()
    for drop_path in (
        paths.DEFAULT_RESOLVE_DROP,
        paths.DEFAULT_ENRICH_DROP,
        paths.DEFAULT_ENV_DROP,
        paths.DEFAULT_REFERENCE_DROP,
        paths.DEFAULT_CURATE_DROP,
    ):
        dropped_ids |= _drop_ids(drop_path)

    done = _repo_counts(paths.DEFAULT_CURATE_JSONL)
    pending: dict[str, int] = {}
    for row in load_jsonl(paths.DEFAULT_COLLECT_JSONL):
        tid, repo = row.get("task_id"), row.get("repo")
        if not tid or not repo or tid in curate_ids or tid in dropped_ids:
            continue
        pending[repo] = pending.get(repo, 0) + 1

    repos = set(done) | set(pending)
    return {repo: {"done": done.get(repo, 0), "pending": pending.get(repo, 0)} for repo in repos}


def next_stage(allowed: list[str]) -> str | None:
    # Prefer draining later stages first so a finished env/ref batch can curate
    # before we spend more collect/resolve work.
    for stage in ("curate", "reference", "env", "enrich", "resolve"):
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
    if data_dir and stage in {"collect", "resolve", "enrich", "curate"}:
        # curate accepts --data-dir to refill prompts during intent extraction.
        cmd += ["--data-dir", str(data_dir)]
    print(f"\n=== {stage} ===\n+ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(PIPELINE.parent))


def run_collect_balanced(*, target: int, data_dir: Path) -> int:
    """Run collect so the ``target`` curated tasks end up spread evenly across repos.

    Passes each repo's current {done, pending} counts (see ``repo_progress``)
    so collect only tops up repos under their fair share, and redistributes
    share from repos that have run out of candidates.
    """
    progress_path = paths.COLLECT_OUT / "repo_progress.json"
    write_json(progress_path, repo_progress())
    cmd = [
        sys.executable,
        str(_script("collect")),
        "--data-dir",
        str(data_dir),
        "--balance-target",
        str(target),
        "--repo-progress",
        str(progress_path),
    ]
    print(f"\n=== collect (balanced, target={target}) ===\n+ {' '.join(cmd)}")
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
    args = parser.parse_args()

    if args.only:
        if args.only in {"collect", "resolve"} and not args.data_dir:
            print("--data-dir required for collect/resolve", file=sys.stderr)
            return 2
        if args.only == "collect" and args.limit:
            return run_collect_balanced(target=args.limit, data_dir=args.data_dir)
        return run_stage(args.only, limit=args.limit or 0, data_dir=args.data_dir)

    allowed = select_stages(args)
    if "collect" in allowed and not args.data_dir:
        print("--data-dir required when running collect", file=sys.stderr)
        return 2

    target = args.limit or 0
    before = len(_task_ids(paths.DEFAULT_CURATE_JSONL))
    while True:
        curated = len(_task_ids(paths.DEFAULT_CURATE_JSONL))
        if target and (curated - before) >= target:
            print(f"reached target +{curated - before} curated specs")
            return 0
        stage = next_stage(allowed)
        if stage is None:
            print("no pending work")
            return 0
        # next_stage falls through to collect when the backlog is empty. Without a
        # curated target that would loop forever; with a target, keep collecting
        # until capacity runs out or the target is hit.
        if stage == "collect":
            if not target:
                print("no pending work")
                return 0
            before_collected = len(_task_ids(paths.DEFAULT_COLLECT_JSONL))
            rc = run_collect_balanced(target=target, data_dir=args.data_dir)
            if rc != 0:
                print(f"stage collect exited {rc}", file=sys.stderr)
                return rc
            if len(_task_ids(paths.DEFAULT_COLLECT_JSONL)) == before_collected:
                print(
                    "no repo capacity left to reach target "
                    f"(+{curated - before}/{target} curated so far)",
                    file=sys.stderr,
                )
                return 1
            continue
        # When draining, attempt one task at a time.
        rc = run_stage(stage, limit=1, data_dir=args.data_dir)
        if rc != 0:
            print(f"stage {stage} exited {rc}", file=sys.stderr)
            return rc


if __name__ == "__main__":
    raise SystemExit(main())
