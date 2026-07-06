# Training: eval + evolver for the workflow agents

The evolver improves **Coder** and **Reviewer** policy (legacy keys `l4` / `l3`)
from optimizer trace JSONL files — not from the old L34 shared worklog.

## What `train` optimizes today

- `train` runs the **coder↔reviewer** loop (`loop_fn`, default `layer="l4"`),
  reconstructs rounds from **`agent-state/workflow/optimizer-traces/*.jsonl`**,
  grades final diffs with hidden tests, and proposes updates to coder/reviewer
  guidance.
- Full **workflow** trace mining (router/planner/presenter) is still evolving.
  Eval hooks accept legacy `--role` names (`l4`, `l3`, `pm`, `loop`).

Default task source for eval/train: on-disk specs in `evals/langbridge-bench/specs/`
(`--source langbridge-bench`; `swebench` is a backward-compat alias). Use
`--source local` + `LANGBRIDGE_TARGET_REPO` for a git repo with cached specs.


## What is built (and tested)

Pure, unit-tested logic (no API/model needed — see `tests/unit/test_training_*.py`):

- `policy.py` (in the package root) — the mutable policy the evolver writes and the
  roles read. Guidance per role (`pm/l4/l5/l3`), skills (reuses the existing
  `read_skill` tool), checkpoints. Injected into prompts via `policy.apply(role, base)`.
- `metrics.py` — `compute_metrics` + `record_result` + leaderboard for the five
  eval types (`l4`, `l5`, `l3`, `pm`, `loop`).
- `signals.py` — responsiveness, alignment (LLM-judged, judge injected),
  calibration, and batch pattern mining.
- `bench.py` — the test-based ground-truth judge (F2P/P2P over hidden tests),
  plus `build_spec` to derive specs from a repo's real fix commits.
- `gate.py` — applies an evolver proposal to the policy (with the **reviewer anchor
  gate**) and the **acceptance gate** (keep a change only if it improves the
  penalty score; reward-hacking is the worst outcome).
- `evals/runner.py` — the five eval runners as pure orchestration over injected
  agent callables.
- `l3_cases.py` — expands each task spec into gold / no-fix reviewer cases with
  test-based `gt_pass` labels for `eval --role l3`.
- `evolver.py` — the outer self-play loop (run batch → grade → mine → propose →
  apply → gate → checkpoint).

Integration wiring (depends on the chosen target repo + model; not unit-tested):

- `evals/agents_adapter.py` + `evals/_run_layer.py` — drive the REAL agents by
  running each layer in a subprocess whose cwd is a fresh git worktree of the
  target repo at the task's base commit, then capturing `git diff`.
- `cli.py` — `specs`, `eval`, and `train` commands.

## Anti-pathology guards (carried over)

- **Reward hacking**: surfaced as a label; the gate penalises "approved but tests
  fail" worst (−3), so a change that games L3 is rejected.
- **Reviewer collapse**: L3 guidance/skills only change when the batch has a
  trustworthy correctness anchor (real tests or a unanimous jury).
- **Prompt bloat / mode collapse**: guidance is deduped and capped per role; the
  evolver can remove/replace bullets, not only add.
- **Oracle leak**: guidance mentioning hidden signals (ground truth, hidden tests,
  the jury, F2P/P2P…) is stripped before it reaches a prompt.
- **Ground-truth anchoring**: correctness is decided by hidden regression tests,
  computed offline and never shown to the agents.

## How to run

Default (validated pytest dataset):

```bash
export GITHUB_TOKEN=...                             # optional; raises API limits for dataset rebuild
export LANGBRIDGE_MODEL=...                         # agent model

# Evaluate one role under the current policy
uv run python -m langbridge_cli.training.cli eval --role l4 --limit 5
uv run python -m langbridge_cli.training.cli eval --role l3 --limit 5   # reviewer: gold + no-fix per task
uv run python -m langbridge_cli.training.cli eval --role loop --limit 5 # same trace shape as train

# Evolver epoch (L4/L3 policy only today)
uv run python -m langbridge_cli.training.cli train --epochs 1 --batch-size 2

# Evaluate a frozen checkpoint instead of the live policy
LANGBRIDGE_POLICY_DIR=training/policy/checkpoints/epoch1 \
  uv run python -m langbridge_cli.training.cli eval --role l3
```

Local git repo + specs cache:

```bash
export LANGBRIDGE_TARGET_REPO=./arrow            # a git repo with bug-fix history
export LANGBRIDGE_SPECS_DIR=training/specs

# Build F2P/P2P specs from real fix commits
uv run python -m langbridge_cli.training.cli specs --issues training/issues.json

uv run python -m langbridge_cli.training.cli eval --role l4 --limit 5 --source local
uv run python -m langbridge_cli.training.cli train --epochs 1 --batch-size 2 --source local
```

`issues.json` is a list of `{task_id, fix_commit, title, body_summary, hard?}`.

## Open decisions / future work

1. **L5 + PM traces for `train`.** Wire `loop_fn(layer="l5")` on hard tasks and
   `pm_fn` for outer-loop traces; mine PM/L5-specific signals from worklogs.
2. **Target repo / task set.** Default is `evals/langbridge-bench/specs/`; local
   git-derived specs remain supported via `--source local`.
3. **Model + cost.** Defaults are expensive for many self-play loops; set
   `LANGBRIDGE_MODEL` / `--model` (and `--evolver-model`) as needed.
4. **Per-round diffs.** The subprocess adapter reconstructs verdicts/comments from
   the shared worklog, but not full per-round diffs, so responsiveness/alignment
   from that path are approximate.
