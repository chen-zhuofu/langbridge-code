# SWE-bench e2e eval (L4)

Measures the langbridge CLI end to end on real GitHub issues. Right now the CLI's
only worker is the L4 engineer (with L3 review), so this is the **L4-only** eval.
L5 and L4+L5 evals come later.

## How it works

One SWE-bench **instance** = one real issue: a repo, a `base_commit`, the issue
text, and hidden tests. The eval runs in two stages.

## Datasets by difficulty

Both runners take `--difficulty {lite,verified,pro}` (default `lite`). It just
selects the Hugging Face dataset; `--dataset <id>` still overrides it.

| `--difficulty` | Dataset | Size | Notes |
| --- | --- | --- | --- |
| `lite` (easy) | `princeton-nlp/SWE-bench_Lite` | ~300 | Self-contained tasks; cheap, fast iteration. |
| `verified` (medium) | `princeton-nlp/SWE-bench_Verified` | 500 | Human-validated; cleanest set for comparisons. |
| `pro` (hard) | `ScaleAI/SWE-bench_Pro` | 731 public | Enterprise, long-horizon tasks. |

### Commands per benchmark

Each benchmark = Stage 1 (generate predictions) + Stage 2 (grade). Bump
`--count` once a smoke run looks good.

**Lite (easy):**

```bash
# Stage 1 — generate
sg docker -c "uv run python evals/swebench/run_eval_docker.py --difficulty lite --count 10"
# Stage 2 — grade (run from evals/swebench so grader logs land in the eval area)
cd evals/swebench && uv run python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path out/predictions.jsonl \
  --max_workers 4 --run_id langbridge-l4-lite
```

**Verified (medium):**

```bash
# Stage 1 — generate
sg docker -c "uv run python evals/swebench/run_eval_docker.py --difficulty verified --count 10"
# Stage 2 — grade (run from evals/swebench so grader logs land in the eval area)
cd evals/swebench && uv run python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path out/predictions.jsonl \
  --max_workers 4 --run_id langbridge-l4-verified
```

**Pro (hard):**

```bash
# Stage 1 — generate (host runner; see Pro caveat below)
uv run python evals/swebench/run_eval.py --difficulty pro --count 10
# Stage 2 — grade with Scale's harness, not the swebench grader
# https://github.com/scaleapi/SWE-bench_Pro-os
```

**Pro caveat:** prediction generation works the same (it follows the Verified
schema), but Pro is graded with Scale's own harness and per-instance images
(each row carries a `dockerhub_tag`), **not** the `swebench` Docker namespace.
So `run_eval_docker.py --difficulty pro` won't resolve images and the Stage 2
`swebench` grader doesn't apply to Pro; use the host runner for predictions and
grade via [scaleapi/SWE-bench_Pro-os](https://github.com/scaleapi/SWE-bench_Pro-os).

### Stage 1 — generate predictions

There are two runners. Prefer the Docker one.

#### Recommended: agent-inside-image (sandbox, Docker)

```bash
sg docker -c "uv run python evals/swebench/run_eval_docker.py --count 10"
```

This runs the agent **inside each instance's official SWE-bench image** — the
repo is checked out at `base_commit` *with all dependencies installed*, so the
agent's `run_tests` / `execute_program` calls actually work and it can verify
its own fix. Per instance it:
1. pulls the prebuilt image (`swebench` namespace) if missing,
2. starts a container and copies the langbridge source in,
3. installs `openai` into the container's `testbed` conda env (the only runtime
   dep the headless path needs; numpy/textual/prompt_toolkit are TUI-only),
4. runs the headless agent in `/testbed` with the issue text on stdin,
5. captures `git diff` as the `model_patch`,
6. writes `evals/swebench/out/predictions.jsonl` and `run_summary.json`.

The agent runs under the container's `testbed` Python (3.9). Our code is 3.9
compatible, so `run_tests` (which uses `sys.executable -m pytest`) targets the
repo's real test environment.

Requires Docker access (be in the `docker` group, or wrap with `sg docker -c`).
Options: `--dataset`, `--split`, `--count`, `--namespace`, `--model`,
`--timeout`, `--out`.

#### Legacy: host checkout (no Docker, no deps)

```bash
uv run python evals/swebench/run_eval.py --count 10
```

Same idea but it shallow-fetches the repo onto the host **without installing
dependencies**, so the agent codes blind and usually produces an empty patch.
Kept for reference; see "Known limitation" below.

Eval artifacts (session logs, `todo_list.md`) are redirected out of the repo via
`LANGBRIDGE_RUNS_DIR` / `LANGBRIDGE_TODO_LIST_PATH`, so they don't pollute the patch.

### Stage 2 — grade (needs Docker)

The official SWE-bench grader runs each repo's hidden tests inside per-instance
Docker images. Install Docker first, then run it **from `evals/swebench/`** so the
grader's `logs/run_evaluation/` land in the eval area instead of the repo root:

```bash
cd evals/swebench && uv run python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path out/predictions.jsonl \
  --max_workers 4 \
  --run_id langbridge-l4-run1
```

This writes a report with how many instances were **resolved** (FAIL_TO_PASS now
pass and PASS_TO_PASS still pass). The per-instance logs go to
`evals/swebench/logs/run_evaluation/<run_id>/...` (gitignored).

## Known limitation (legacy host runner only)

`run_eval.py` runs in a plain checkout **without the repo's dependencies
installed**, so its own `run_tests` / `execute_program` calls may fail. L4 can
still read code and produce a patch, but it cannot verify against the real test
environment. This caps quality. The Docker runner (`run_eval_docker.py`) fixes
this by running the agent inside the same per-instance image used for grading.
