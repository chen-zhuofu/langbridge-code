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
EVAL_PKG = BENCH.parent / "eval"
for _p in (PIPELINE, BENCH, EVAL_PKG):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib import paths  # noqa: E402
from _lib.bench_config import eval_run_metadata  # noqa: E402
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
    run_config: dict | None = None,
) -> dict:
    tid = spec["task_id"]
    if stub:
        # Stub runs are offline plumbing checks and are never scored, so a
        # missing sim LLM should not void them.
        spec.setdefault("sim", {})["allow_offline"] = True
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
        if episode.get("sim_error"):
            # Void episode — the sim never spoke, so grading measures nothing.
            _log(f"  {tid}: SIM ERROR: {episode['sim_error']} (skipping grade)")
        elif not stub and not no_grade and hasattr(agent, "grade"):
            _log(f"  {tid}: capturing diff + grading F2P...")
            agent.capture_diff()
            grade = agent.grade(timeout=grade_timeout)
            tests_passed = bool(grade.get("tests_passed"))
            _log(
                f"  {tid}: grade tests_passed={tests_passed} "
                f"f2p={grade.get('f2p_passed')}/{grade.get('f2p_total')}"
            )
        # LLM coverage judge on real runs only; stub runs stay offline.
        scored = score_episode(
            spec, episode, tests_passed=tests_passed, judge_coverage=not stub
        )
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
        {
            "task_id": tid,
            "config": run_config,
            "episode": episode,
            "score": scored,
            "grade": grade,
        },
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
    run_config = eval_run_metadata(agent_model=args.model, stub=bool(args.stub))
    print(f"Running {len(specs)} tasks with {workers} workers")
    print(
        f"models: agent={run_config['agent']['model']} "
        f"sim={run_config['interactive']['sim_model']} "
        f"coverage={run_config['interactive']['coverage_model']}"
    )

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
                run_config=run_config,
            ): spec
            for spec in specs
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    # Sim failures are not agent failures: keep them out of the pass-rate denominator.
    voided = [r for r in results if r.get("sim_error") or r.get("error")]
    voided_ids = {id(r) for r in voided}
    scorable = [r for r in results if id(r) not in voided_ids]
    passed = [r for r in scorable if r.get("pass")]
    report = {
        "n": len(results),
        "n_scorable": len(scorable),
        "n_voided": len(voided),
        "n_passed": len(passed),
        "pass_rate": (len(passed) / len(scorable)) if scorable else None,
        "voided": [
            {"task_id": r.get("task_id"), "reason": r.get("sim_error") or r.get("error")}
            for r in voided
        ],
        "workers": workers,
        "config": run_config,
        "results": results,
        "created_at": stamp,
        "stub": bool(args.stub),
    }
    write_json(out_dir / "report.json", report)
    if voided:
        print(f"WARNING: {len(voided)}/{len(results)} episodes voided (not agent failures):")
        for row in voided:
            print(f"  {row.get('task_id')}: {row.get('sim_error') or row.get('error')}")
    print(f"pass {len(passed)}/{len(scorable)} scorable ({len(voided)} voided)")
    print(f"report: {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
