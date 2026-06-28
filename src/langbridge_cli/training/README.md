# Training: eval + evolver for PM / L4 / L5 / L3

This subsystem distills the "next-door" coder/reviewer self-play worktrial into
this repo. The idea is the same two-loop design:

- **Inner loop** (already in this repo): for one task, L4/L5 implement and L3
  reviews, back and forth, until L3 passes or limits trip. Agent skills are fixed
  here.
- **Outer loop** (new, here): across many tasks, an **evolver** mines signals from
  the traces and improves the agents — not by editing code, but by editing a
  shared **policy** (extra guidance bullets + skills) that each role folds into its
  prompt.

The mapping from next-door to this repo:

| next-door | here |
|-----------|------|
| coder | **L4** (normal task) and **L5** (hard task, divide-and-conquer) |
| reviewer | **L3** (tester) |
| loop | the L4↔L3 and L5↔L3 inner review loops |
| (none) | **PM** — top-level decompose → route L4/L5 → e2e, evaluated too |

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

## How to run (once a target repo is chosen)

```bash
export LANGBRIDGE_TARGET_REPO=./arrow            # a git repo with bug-fix history
export LANGBRIDGE_SPECS_DIR=training/specs
export LANGBRIDGE_MODEL=...                       # agent model (see open question)

# 1. Build F2P/P2P specs from real fix commits
python -m langbridge_cli.training.cli specs --issues training/issues.json

# 2. Evaluate one role under the current policy
python -m langbridge_cli.training.cli eval --role l4 --limit 5
python -m langbridge_cli.training.cli eval --role l3 --limit 5   # reviewer: gold + no-fix per task

# 3. Run the evolver (self-play) for one epoch
python -m langbridge_cli.training.cli train --epochs 1 --batch-size 2

# Evaluate a frozen checkpoint instead of the live policy
LANGBRIDGE_POLICY_DIR=training/policy/checkpoints/epoch1 \
  python -m langbridge_cli.training.cli eval --role l3
```

`issues.json` is a list of `{task_id, fix_commit, title, body_summary, hard?}`.

## Open decisions (need confirmation)

1. **Target repo / task set.** Reuse next-door's `arrow` repo + 25 issues (small,
   fast, cheap — best for many self-play loops), point at the existing SWE-bench
   wiring (heavy/Docker), or curate a custom set? The grader is pluggable; default
   is the arrow-style git F2P/P2P grader.
2. **Model + cost.** This repo defaults to `gpt-5.1-codex`, which is expensive for
   the many loop runs an evolver needs. Use a cheaper model for eval/evolver
   (set `LANGBRIDGE_MODEL` / `--model`)?
3. **Per-round diffs.** The subprocess adapter reconstructs verdicts/comments from
   the shared worklog, but not per-round diffs, so responsiveness/alignment from
   that path are approximate. Worth instrumenting the loop to emit a structured
   trace if those signals matter.
