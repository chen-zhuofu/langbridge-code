"""Run the full dataset pipeline: collect → env → reference → curate.

```bash
uv run python data-pipeline/run_pipeline.py
uv run python data-pipeline/run_pipeline.py --limit 1
uv run python data-pipeline/run_pipeline.py --from reference
uv run python data-pipeline/run_pipeline.py --only curate
```

``--limit N`` = each stage attempts at most N **new** tasks (already in that
stage's out/drop do not count). Collect resumes by appending.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE.parent
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
        help="at most N new tasks per stage (0 = no cap)",
    )
    args = parser.parse_args(argv)
    stages = select_stages(args)
    print(f"Pipeline: {' → '.join(stages)}")
    for stage in stages:
        cmd = _cmd(stage, limit=args.limit)
        print(f"\n=== {stage} ===\n+ {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=str(REPO_ROOT))
        if result.returncode != 0:
            print(f"\nStage {stage!r} failed with exit {result.returncode}", file=sys.stderr)
            return result.returncode

    print("\nPipeline done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
