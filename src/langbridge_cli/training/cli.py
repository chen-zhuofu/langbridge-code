"""cli.py — run the multi-role eval harness.

Examples (see training/README.md):

  python -m langbridge_cli.training.cli eval --role l4 --limit 5
  python -m langbridge_cli.training.cli eval --role l3 --source swebench
"""
import argparse
import os

from langbridge_cli import policy
from langbridge_cli.config import DEFAULT_MODEL
from langbridge_cli.training import bench, metrics, swebench
from langbridge_cli.training.evals import agents_adapter, runner


def _build(args, model):
    if getattr(args, "source", "swebench") == "local":
        grade = bench.make_git_grader()
        calls = agents_adapter.make_callables(model=model)

        def specs_for(hard=None):
            return bench.list_specs(hard=hard)
        return specs_for, grade, calls

    ws = swebench.Workspaces()
    grade = swebench.make_grader(ws)
    calls = swebench.make_callables(ws, model=model)

    def specs_for(hard=None):
        return swebench.specs(hard=hard)
    return specs_for, grade, calls


def _limit(specs, args):
    if not specs:
        raise SystemExit("No specs found. Check the dataset / --source (see training/README.md).")
    return specs[: args.limit] if args.limit else specs


def cmd_eval(args):
    model = args.model or os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    specs_for, grade, calls = _build(args, model)
    p = policy.load()

    if args.role == "l4":
        rows = runner.eval_l4(_limit(specs_for(hard=False), args), coder_fn=calls["coder_fn"], grade=grade)
    elif args.role == "l5":
        rows = runner.eval_l5(_limit(specs_for(hard=True), args), coder_fn=calls["l5_fn"], grade=grade)
    elif args.role == "l3":
        rows = runner.eval_l3(_limit(specs_for(), args), review_fn=calls["review_fn"])
    elif args.role == "loop":
        rows, _traces = runner.eval_loop(_limit(specs_for(), args), loop_fn=calls["loop_fn"], grade=grade)
    elif args.role == "pm":
        rows = runner.eval_pm(_limit(specs_for(), args), pm_fn=calls["pm_fn"], grade=grade)
    else:
        raise SystemExit(f"unknown role {args.role}")

    path = metrics.record_result(
        args.role, rows, model=model,
        dataset=os.environ.get("LANGBRIDGE_TARGET_REPO", ""),
        policy_version=p.get("version"),
    )
    metrics.write_leaderboard()
    print(f"metrics: {metrics.compute_metrics(args.role, rows)}")
    print(f"recorded: {path}")


def cmd_specs(args):
    import json

    with open(args.issues) as f:
        issues = json.load(f)
    ok, statuses = bench.build_specs(issues)
    print(f"built {ok} ok specs out of {len(issues)} issues")
    for tid, st in statuses.items():
        if st != "ok":
            print(f"  {tid}: {st}")


def main():
    ap = argparse.ArgumentParser(prog="langbridge-train")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("specs", help="build F2P/P2P specs from a repo's fix commits")
    ps.add_argument("--issues", required=True,
                    help="JSON list of {task_id, fix_commit, title, body_summary, hard?}")
    ps.set_defaults(func=cmd_specs)

    pe = sub.add_parser("eval", help="evaluate one role")
    pe.add_argument("--role", required=True, choices=["l4", "l5", "l3", "loop", "pm"])
    pe.add_argument("--source", default="swebench", choices=["swebench", "local"])
    pe.add_argument("--model", default=None)
    pe.add_argument("--limit", type=int, default=0)
    pe.set_defaults(func=cmd_eval)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
