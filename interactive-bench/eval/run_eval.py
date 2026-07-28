"""Interactive-bench eval runner.

Loads specs from ``interactive-bench/data/specs/``, drives the sim harness,
and writes reports under ``interactive-bench/eval/out/``.

```bash
uv run python interactive-bench/eval/run_eval.py --stub --limit 1
uv run python interactive-bench/eval/run_eval.py --task <id>
uv run python interactive-bench/eval/run_eval.py --workers 2 --offset 1 --limit 2
```
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import threading
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
from _lib.runtime import eval_timeout_sec  # noqa: E402
from harness.agent import make_docker_agent, make_stub_agent  # noqa: E402
from harness.score import score_episode  # noqa: E402
from harness.sim import run_episode  # noqa: E402

_PRINT_LOCK = threading.Lock()


def _e2e_budget_sec(spec: dict) -> int:
    """Per-task e2e ceiling: max(40m, 2×baseline runtime). Refresh sim.timeout_sec."""
    runtime = (spec.get("baseline") or {}).get("agent_runtime_sec")
    budget = int(eval_timeout_sec(runtime))
    sim = spec.setdefault("sim", {})
    sim["timeout_sec"] = float(budget)
    return budget


def load_specs(
    *,
    task_id: str | None = None,
    limit: int = 0,
    offset: int = 0,
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
        _e2e_budget_sec(data)
        specs.append(data)
    if offset:
        specs = specs[offset:]
    if limit:
        specs = specs[:limit]
    return specs


def _log(msg: str) -> None:
    with _PRINT_LOCK:
        print(msg, flush=True)


def run_one_spec(
    spec: dict,
    *,
    out_dir: Path,
    stub: bool,
    turn_timeout: int | None,
    max_turns: int,
    grade_timeout: int,
    no_grade: bool,
    model: str | None,
) -> dict:
    tid = spec["task_id"]
    budget = _e2e_budget_sec(spec)
    # Default turn budget = e2e ceiling so a single long turn is not cut at 15m.
    effective_turn = int(turn_timeout) if turn_timeout is not None else budget
    _log(f"=== {tid} === (e2e={budget}s turn={effective_turn}s)")
    agent_artifacts = out_dir / tid
    agent_artifacts.mkdir(parents=True, exist_ok=True)
    agent = (
        make_stub_agent()
        if stub
        else make_docker_agent(
            spec,
            artifacts_dir=agent_artifacts,
            turn_timeout_sec=effective_turn,
            model=model,
        )
    )
    grade = None
    try:
        if hasattr(agent, "start"):
            _log(f"  {tid}: starting docker main agent...")
            agent.start()
        episode = run_episode(spec, agent, max_turns=max_turns)
        tests_passed = None
        if not stub and not no_grade and hasattr(agent, "grade"):
            _log(f"  {tid}: capturing diff + grading F2P...")
            agent.capture_diff()
            grade = agent.grade(timeout=grade_timeout)
            tests_passed = bool(grade.get("tests_passed"))
            _log(
                f"  {tid}: grade tests_passed={tests_passed} "
                f"f2p={grade.get('f2p_passed')}/{grade.get('f2p_total')}"
            )
        scored = score_episode(spec, episode, tests_passed=tests_passed)
    except Exception as exc:  # noqa: BLE001
        _log(f"  {tid}: error: {exc}")
        row = {"task_id": tid, "error": str(exc), "pass": False}
        write_json(out_dir / f"{tid}.json", {"task_id": tid, "error": str(exc)})
        return row
    finally:
        if hasattr(agent, "close"):
            try:
                agent.close()
            except Exception:  # noqa: BLE001
                pass

    write_json(
        out_dir / f"{tid}.json",
        {"task_id": tid, "episode": episode, "score": scored, "grade": grade},
    )
    summary = {"task_id": tid, **scored, "grade": grade}
    _log(
        f"  {tid}: stop={episode['stop_reason']} inputs={episode['user_input_count']} "
        f"interventions={episode['interventions']} pass={scored.get('pass')}"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=str, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0, help="skip the first N specs")
    parser.add_argument("--workers", type=int, default=4)
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
        default=None,
        help=(
            "Max seconds per main-agent turn. Default: per-task e2e budget "
            "max(40m, 2×baseline agent runtime)."
        ),
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
        offset=args.offset or 0,
        include_demo=args.include_demo,
    )
    if not specs:
        print(f"no specs in {paths.SPECS_DIR}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, int(args.workers))
    print(f"Running {len(specs)} tasks with {workers} workers")

    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                run_one_spec,
                spec,
                out_dir=out_dir,
                stub=bool(args.stub),
                turn_timeout=args.turn_timeout,
                max_turns=args.max_turns,
                grade_timeout=args.grade_timeout,
                no_grade=bool(args.no_grade),
                model=args.model,
            ): spec
            for spec in specs
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    report = {
        "n": len(results),
        "workers": workers,
        "results": results,
        "created_at": stamp,
        "stub": bool(args.stub),
    }
    write_json(out_dir / "report.json", report)
    print(f"report: {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
