"""Run the full dataset pipeline: collect → env → reference → curate.

```bash
uv run python eval/data/data-pipeline/run_pipeline.py
uv run python eval/data/data-pipeline/run_pipeline.py --limit 1
uv run python eval/data/data-pipeline/run_pipeline.py --from reference
uv run python eval/data/data-pipeline/run_pipeline.py --only curate
```

``--limit N`` (full / ``--from`` runs) = keep going until **N new** tasks land
in ``eval/data/langbridge-bench/specs/`` (or the pipeline is stuck: no backlog and collect finds
nothing). Drops and failed attempts do not count; the runner drains pending
work stage-by-stage and only collects when upstream is empty.

``--only STAGE --limit N`` still means that stage's local attempt cap.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE.parent
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.io import dropped_task_ids_from_json, existing_task_ids_from_jsonl  # noqa: E402
from _lib.spec import curate_out_ids, eval_spec_ids  # noqa: E402

STAGES = ("collect", "env", "reference", "curate")


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


def run_until_benches(allowed: list[str], *, target: int) -> int:
    """Run stages until ``target`` new eval specs appear, or stuck."""
    before = eval_spec_ids()
    produced = 0
    rounds = 0
    stagnant = 0

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

        code = run_stage(stage, limit=1)
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
        moved = any(pending_after[s] != pending_before[s] for s in pending_before)
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
