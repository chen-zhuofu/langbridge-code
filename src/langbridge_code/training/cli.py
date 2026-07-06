"""cli.py — run the evals and the evolver.

Examples (after setting the target repo + specs, see training/README.md):

  # Evaluate one role on the spec set under the current policy:
  LANGBRIDGE_TARGET_REPO=./arrow LANGBRIDGE_SPECS_DIR=training/specs \
    python -m langbridge_cli.training.cli eval --role l4 --limit 5

  # Evaluate against a frozen checkpoint instead of the live policy:
  LANGBRIDGE_POLICY_DIR=training/policy/checkpoints/epoch1 \
    python -m langbridge_cli.training.cli eval --role l3

  # Run the evolver (self-play) for one epoch over the spec set:
  python -m langbridge_cli.training.cli train --epochs 1 --batch-size 2
"""
import argparse
import os

from langbridge_cli import policy
from langbridge_cli.settings import (
    DEFAULT_MODEL,
    TRAIN_DEFAULT_BATCH_SIZE,
    TRAIN_DEFAULT_CHECKPOINT_EVERY,
    TRAIN_DEFAULT_EPOCHS,
    load_api_key,
)
from langbridge_cli.training import bench, evolver, langbridge_bench, metrics
from langbridge_cli.training.evals import agents_adapter, runner
from langbridge_cli.training.l3_cases import l3_cases_from_specs


def _build(args, model):
    """Return (specs_for(hard), grade, calls) for the chosen task source.

    Default source is langbridge-bench (on-disk specs under evals/langbridge-bench/specs/).
    `--source local` uses a local git repo + cached specs (see bench.py).
    """
    source = getattr(args, "source", "langbridge-bench")
    if source == "swebench":
        source = "langbridge-bench"
    if source == "local":
        grade = bench.make_git_grader()
        calls = agents_adapter.make_callables(model=model)

        def specs_for(hard=None):
            return bench.list_specs(hard=hard)
        return specs_for, grade, calls

    ws = langbridge_bench.Workspaces()
    grade = langbridge_bench.make_grader(ws)
    calls = langbridge_bench.make_callables(ws, model=model)

    def specs_for(hard=None):
        return langbridge_bench.specs(hard=hard)
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
        specs = _limit(specs_for(), args)
        cases = l3_cases_from_specs(specs, grade)
        print(f"L3 reviewer: {len(cases)} cases ({len(specs)} tasks × gold + no_fix)")
        rows = runner.eval_l3(cases, review_fn=calls["review_fn"])
    elif args.role == "loop":
        rows, _traces = runner.eval_loop(_limit(specs_for(), args), loop_fn=calls["loop_fn"], grade=grade)
    elif args.role == "pm":
        rows = runner.eval_pm(_limit(specs_for(), args), pm_fn=calls["pm_fn"], grade=grade)
    else:
        raise SystemExit(f"unknown role {args.role}")

    path = metrics.record_result(args.role, rows, model=model,
                                 dataset=os.environ.get("LANGBRIDGE_TARGET_REPO", ""),
                                 policy_version=p.get("version"))
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


def cmd_train(args):
    api_key = load_api_key()
    model = args.model or os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    evolver_model = args.evolver_model or model
    specs_for, grade, calls = _build(args, model)
    evolve_fn = evolver.make_evolve_fn(api_key, evolver_model)
    jury_fn = None
    if not args.no_jury:
        from langbridge_cli.training import jury as training_jury

        jury_fn = training_jury.make_jury_fn(api_key, model)
    specs = _limit(specs_for(), args)
    results = evolver.run(
        specs, loop_fn=calls["loop_fn"], grade=grade, evolve_fn=evolve_fn,
        jury_fn=jury_fn,
        epochs=args.epochs, batch_size=args.batch_size,
        do_gate=not args.no_gate, checkpoint_every=args.checkpoint_every,
    )
    accepted = sum(1 for r in results if r["accepted"])
    print(f"batches: {len(results)}  accepted: {accepted}  policy v{policy.load().get('version')}")


def main():
    ap = argparse.ArgumentParser(prog="langbridge-train")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("specs", help="build F2P/P2P specs from a repo's fix commits")
    ps.add_argument("--issues", required=True,
                    help="JSON list of {task_id, fix_commit, title, body_summary, hard?}")
    ps.set_defaults(func=cmd_specs)

    pe = sub.add_parser("eval", help="evaluate one role")
    pe.add_argument("--role", required=True, choices=["l4", "l5", "l3", "loop", "pm"])
    pe.add_argument("--source", default="langbridge-bench",
                    choices=["langbridge-bench", "swebench", "local"],
                    help="langbridge-bench (default), swebench (alias), or local git specs")
    pe.add_argument("--model", default=None)
    pe.add_argument("--limit", type=int, default=0)
    pe.set_defaults(func=cmd_eval)

    pt = sub.add_parser("train", help="run the evolver (self-play)")
    pt.add_argument("--source", default="langbridge-bench",
                    choices=["langbridge-bench", "swebench", "local"],
                    help="langbridge-bench (default), swebench (alias), or local git specs")
    pt.add_argument("--model", default=None, help="agent model")
    pt.add_argument("--evolver-model", default=None, help="model for the evolver itself")
    pt.add_argument("--epochs", type=int, default=TRAIN_DEFAULT_EPOCHS)
    pt.add_argument("--batch-size", type=int, default=TRAIN_DEFAULT_BATCH_SIZE)
    pt.add_argument("--no-gate", action="store_true", help="skip the acceptance gate")
    pt.add_argument("--no-jury", action="store_true", help="skip offline jury when tests are unavailable")
    pt.add_argument("--checkpoint-every", default=TRAIN_DEFAULT_CHECKPOINT_EVERY, choices=["batch", "epoch"])
    pt.add_argument("--limit", type=int, default=0)
    pt.set_defaults(func=cmd_train)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
