"""Run the full dataset pipeline: collect → env → reference → curate.

```bash
uv run python eval/data-pipeline/run_pipeline.py
uv run python eval/data-pipeline/run_pipeline.py --limit 1
uv run python eval/data-pipeline/run_pipeline.py --from reference
uv run python eval/data-pipeline/run_pipeline.py --only curate
```

``--limit N`` (full / ``--from`` runs) = keep going until **N new** tasks land
in ``eval/data/langbridge-bench/specs/`` (or the pipeline is stuck: no backlog and collect finds
nothing). Drops and failed attempts do not count; the runner drains pending
work stage-by-stage and only collects when upstream is empty.

``--only STAGE --limit N`` still means that stage's local attempt cap.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE.parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.io import (  # noqa: E402
    dropped_task_ids_from_json,
    existing_task_ids_from_jsonl,
    load_drop_entries,
    load_json,
    load_jsonl,
)
from _lib.spec import curate_out_ids, eval_spec_ids  # noqa: E402

STAGES = ("collect", "env", "reference", "curate")
STRUCTURAL_ENV_ERRORS = (
    "does not appear to be a Python project",
)
MAX_FAILED_ATTEMPTS_PER_EMPTY_REPO = 5


def _script(stage: str) -> Path:
    return {
        "collect": PIPELINE / "collect" / "collect.py",
        "env": PIPELINE / "env" / "build_env.py",
        "reference": PIPELINE / "reference" / "reference_test.py",
        "curate": PIPELINE / "curate" / "curate.py",
    }[stage]


def _cmd(stage: str, *, limit: int) -> list[str]:
    cmd = [sys.executable, str(_script(stage))]
    if limit:
        cmd += ["--limit", str(limit)]
    return cmd


def select_stages(args: argparse.Namespace) -> list[str]:
    if args.only:
        return [args.only]
    start = STAGES.index(args.start_from)
    return list(STAGES[start:])


def _task_ids(jsonl: Path) -> set[str]:
    return existing_task_ids_from_jsonl(jsonl)


def _drop_ids(path: Path) -> set[str]:
    return dropped_task_ids_from_json(path)


def pending_for(stage: str) -> set[str]:
    """Ids waiting to be attempted at ``stage`` (skips already decided)."""
    collect = _task_ids(paths.DEFAULT_COLLECT_JSONL)
    env_out = _task_ids(paths.DEFAULT_ENV_JSONL)
    env_drop = _drop_ids(paths.DEFAULT_ENV_DROP)
    ref_out = _task_ids(paths.DEFAULT_REFERENCE_JSONL)
    ref_drop = _drop_ids(paths.DEFAULT_REFERENCE_DROP)
    curate_out = curate_out_ids()
    curate_drop = _drop_ids(paths.DEFAULT_CURATE_DROP)

    if stage == "env":
        return collect - env_out - env_drop
    if stage == "reference":
        return env_out - ref_out - ref_drop
    if stage == "curate":
        return ref_out - curate_out - curate_drop
    if stage == "collect":
        return set()
    raise ValueError(stage)


def next_stage(allowed: list[str]) -> str | None:
    """Prefer draining downstream backlog before collecting more."""
    for stage in ("curate", "reference", "env"):
        if stage in allowed and pending_for(stage):
            return stage
    if "collect" in allowed:
        return "collect"
    return None


def run_stage(stage: str, *, limit: int = 1) -> int:
    cmd = _cmd(stage, limit=limit)
    print(f"\n=== {stage} ===\n+ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return result.returncode


def repo_progress(
    *,
    before_specs: set[str],
    before_collect: set[str],
) -> dict[str, dict[str, int]]:
    """Count successful and in-flight tasks created during this pipeline run."""
    current_specs = eval_spec_ids()
    new_spec_ids = current_specs - before_specs
    progress: dict[str, dict[str, int]] = {}

    for task_id in new_spec_ids:
        task_path = paths.SPECS_DIR / f"{task_id}.json"
        if not task_path.exists():
            continue
        repo = load_json(task_path).get("repo")
        if repo:
            progress.setdefault(
                repo,
                {"done": 0, "pending": 0, "failed": 0},
            )["done"] += 1

    dropped = (
        _drop_ids(paths.DEFAULT_ENV_DROP)
        | _drop_ids(paths.DEFAULT_REFERENCE_DROP)
        | _drop_ids(paths.DEFAULT_CURATE_DROP)
    )
    structural_env_drops = {
        str(entry["task_id"])
        for entry in load_drop_entries(paths.DEFAULT_ENV_DROP)
        if any(
            marker in str(entry.get("reason", ""))
            for marker in STRUCTURAL_ENV_ERRORS
        )
    }
    for row in load_jsonl(paths.DEFAULT_COLLECT_JSONL):
        task_id = row.get("task_id") or row.get("instance_id")
        repo = row.get("repo")
        if (
            not task_id
            or not repo
            or task_id in before_collect
            or task_id in new_spec_ids
        ):
            continue
        key = "failed" if task_id in dropped else "pending"
        progress.setdefault(
            repo,
            {"done": 0, "pending": 0, "failed": 0},
        )[key] += 1
        if task_id in structural_env_drops:
            progress[repo]["exhausted"] = True

    for info in progress.values():
        if (
            info["done"] == 0
            and info["pending"] == 0
            and info["failed"] >= MAX_FAILED_ATTEMPTS_PER_EMPTY_REPO
        ):
            info["exhausted"] = True

    return progress


def run_collect_balanced(
    *,
    target: int,
    before_specs: set[str],
    before_collect: set[str],
    exhausted_repos: set[str],
) -> tuple[int, set[str]]:
    progress = repo_progress(
        before_specs=before_specs,
        before_collect=before_collect,
    )
    for repo in exhausted_repos:
        info = progress.setdefault(
            repo,
            {"done": 0, "pending": 0, "failed": 0},
        )
        info["exhausted"] = True

    with tempfile.TemporaryDirectory(prefix="lb-collect-") as tmp:
        summary_path = Path(tmp) / "summary.json"
        cmd = [
            sys.executable,
            str(_script("collect")),
            "--balance-target",
            str(target),
            "--repo-progress-json",
            json.dumps(progress, separators=(",", ":")),
            "--summary-json",
            str(summary_path),
        ]
        print(f"\n=== collect (repo-balanced, target={target}) ===\n+ {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=str(REPO_ROOT))
        summary = load_json(summary_path) if summary_path.exists() else {}

    newly_exhausted = {
        repo
        for repo, info in (summary.get("repos") or {}).items()
        if info.get("exhausted")
    }
    return result.returncode, newly_exhausted


def run_until_benches(
    allowed: list[str],
    *,
    target: int,
    before_specs: set[str] | None = None,
    before_collect: set[str] | None = None,
) -> int:
    """Run stages until ``target`` new eval specs appear, or stuck."""
    before = set(before_specs) if before_specs is not None else eval_spec_ids()
    collect_baseline = (
        set(before_collect)
        if before_collect is not None
        else _task_ids(paths.DEFAULT_COLLECT_JSONL)
    )
    produced = 0
    rounds = 0
    stagnant = 0
    exhausted_repos: set[str] = set()

    print(
        f"Goal: produce {target} new bench(es) in {paths.SPECS_DIR} "
        f"(already have {len(before)})"
    )

    while produced < target:
        stage = next_stage(allowed)
        if stage is None:
            print("\n[stuck] no runnable stage in selection; stopping.")
            break

        specs_before = eval_spec_ids()
        collect_before = _task_ids(paths.DEFAULT_COLLECT_JSONL)
        pending_before = {s: pending_for(s) for s in ("env", "reference", "curate")}

        if stage == "collect":
            print(
                f"\n[progress] {produced}/{target} new benches; "
                "no backlog — collecting"
            )
        else:
            print(
                f"\n[progress] {produced}/{target} new benches; "
                f"drain {stage} ({len(pending_before[stage])} pending)"
            )

        if stage == "collect":
            exhausted_before = set(exhausted_repos)
            code, newly_exhausted = run_collect_balanced(
                target=target,
                before_specs=before,
                before_collect=collect_baseline,
                exhausted_repos=exhausted_repos,
            )
            exhausted_repos |= newly_exhausted
            exhaustion_moved = exhausted_repos != exhausted_before
        else:
            code = run_stage(stage, limit=1)
            exhaustion_moved = False
        if code != 0:
            print(f"\nStage {stage!r} failed with exit {code}", file=sys.stderr)
            return code

        rounds += 1
        after = eval_spec_ids()
        produced = len(after - before)
        new_names = sorted(after - specs_before)
        if new_names:
            stagnant = 0
            print(f"[ok] new bench(es): {', '.join(new_names)}  ({produced}/{target})")
            continue

        # No new spec this round — detect stuck (no backlog move, no collect).
        pending_after = {s: pending_for(s) for s in ("env", "reference", "curate")}
        collect_after = _task_ids(paths.DEFAULT_COLLECT_JSONL)
        moved = exhaustion_moved or any(
            pending_after[s] != pending_before[s] for s in pending_before
        )
        collected = len(collect_after - collect_before) > 0
        if moved or collected:
            stagnant = 0
            continue

        stagnant += 1
        if stage == "collect" or stagnant >= 2:
            print(
                "\n[stuck] no new bench and no pipeline progress "
                "(collect empty / backlog exhausted); stopping."
            )
            break

    print(
        f"\nPipeline done. New benches: {produced}/{target} "
        f"→ total specs {len(eval_spec_ids())} ({rounds} stage call(s))"
    )
    return 0 if produced >= target else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="start_from",
        choices=STAGES,
        default="collect",
        help="first stage to run (default: collect)",
    )
    parser.add_argument("--only", choices=STAGES, help="run a single stage")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "produce N new eval/data/langbridge-bench/specs benches (0 = one pass, no target). "
            "With --only, N is that stage's attempt cap."
        ),
    )
    args = parser.parse_args(argv)
    stages = select_stages(args)

    if args.only:
        print(f"Pipeline: {args.only} only")
        code = run_stage(args.only, limit=args.limit)
        if code != 0:
            print(f"\nStage {args.only!r} failed with exit {code}", file=sys.stderr)
        else:
            print("\nPipeline done.")
        return code

    print(f"Pipeline: {' → '.join(stages)}")
    if args.limit and args.limit > 0:
        return run_until_benches(stages, target=args.limit)

    # No target: one linear pass (legacy), each stage uncapped.
    for stage in stages:
        code = run_stage(stage, limit=0)
        if code != 0:
            print(f"\nStage {stage!r} failed with exit {code}", file=sys.stderr)
            return code
    print("\nPipeline done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
