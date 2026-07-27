"""Interactive-bench eval runner.

Loads specs from ``interactive-bench/data/specs/``, drives the sim harness,
and writes reports under ``interactive-bench/eval/out/``.

```bash
uv run python interactive-bench/eval/run_eval.py --stub --limit 1
uv run python interactive-bench/eval/run_eval.py --task <id>   # real main agent
```
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
PIPELINE = BENCH / "data-pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

from _lib import paths  # noqa: E402
from _lib.io_util import write_json  # noqa: E402
from harness.agent import make_docker_agent, make_stub_agent  # noqa: E402
from harness.score import score_episode  # noqa: E402
from harness.sim import run_episode  # noqa: E402


def load_specs(
    *,
    task_id: str | None = None,
    limit: int = 0,
    include_demo: bool = False,
) -> list[dict]:
    paths.SPECS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(paths.SPECS_DIR.glob("*.json"))
    specs = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        tid = str(data.get("task_id") or path.stem)
        if not include_demo and (
            tid.startswith("example__") or path.stem.startswith("example__")
        ):
            continue
        if task_id and data.get("task_id") != task_id and path.stem != task_id:
            continue
        specs.append(data)
        if limit and len(specs) >= limit:
            break
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=str, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--stub", action="store_true", help="Use stub agent (no Docker)")
    parser.add_argument(
        "--include-demo",
        action="store_true",
        help="Include example__* fixture specs (excluded by default)",
    )
    parser.add_argument("--out-dir", type=Path, default=paths.EVAL_OUT)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument(
        "--turn-timeout",
        type=int,
        default=900,
        help="Max seconds per main-agent turn (default 900)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=12,
        help="Max sim/agent turns in an episode (default 12)",
    )
    parser.add_argument(
        "--grade-timeout",
        type=int,
        default=600,
        help="Pytest grade timeout seconds",
    )
    parser.add_argument(
        "--no-grade",
        action="store_true",
        help="Skip F2P grading after the episode",
    )
    args = parser.parse_args()

    specs = load_specs(
        task_id=args.task,
        limit=args.limit or 0,
        include_demo=args.include_demo,
    )
    if not specs:
        print(f"no specs in {paths.SPECS_DIR}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for spec in specs:
        tid = spec["task_id"]
        print(f"=== {tid} ===")
        agent_artifacts = out_dir / tid
        agent_artifacts.mkdir(parents=True, exist_ok=True)
        agent = (
            make_stub_agent()
            if args.stub
            else make_docker_agent(
                spec,
                artifacts_dir=agent_artifacts,
                turn_timeout_sec=args.turn_timeout,
                model=args.model,
            )
        )
        grade = None
        try:
            if hasattr(agent, "start"):
                print("  starting docker main agent...")
                agent.start()
            episode = run_episode(spec, agent, max_turns=args.max_turns)
            tests_passed = None
            if not args.stub and not args.no_grade and hasattr(agent, "grade"):
                print("  capturing diff + grading F2P...")
                agent.capture_diff()
                grade = agent.grade(timeout=args.grade_timeout)
                tests_passed = bool(grade.get("tests_passed"))
                print(
                    f"  grade: tests_passed={tests_passed} "
                    f"f2p={grade.get('f2p_passed')}/{grade.get('f2p_total')}"
                )
            scored = score_episode(spec, episode, tests_passed=tests_passed)
        except Exception as exc:  # noqa: BLE001
            print(f"  error: {exc}", file=sys.stderr)
            write_json(
                out_dir / f"{tid}.json",
                {"task_id": tid, "error": str(exc)},
            )
            results.append({"task_id": tid, "error": str(exc), "pass": False})
            continue
        finally:
            if hasattr(agent, "close"):
                try:
                    agent.close()
                except Exception:  # noqa: BLE001
                    pass

        row = {
            "task_id": tid,
            "episode": episode,
            "score": scored,
            "grade": grade,
        }
        write_json(out_dir / f"{tid}.json", row)
        results.append({"task_id": tid, **scored, "grade": grade})
        print(
            f"  stop={episode['stop_reason']} inputs={episode['user_input_count']} "
            f"interventions={episode['interventions']} pass={scored.get('pass')}"
        )

    report = {
        "n": len(results),
        "results": results,
        "created_at": stamp,
        "stub": bool(args.stub),
    }
    write_json(out_dir / "report.json", report)
    print(f"report: {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
