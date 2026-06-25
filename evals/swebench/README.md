# SWE-bench e2e eval (L4)

Measures the langbridge CLI end to end on real GitHub issues. Right now the CLI's
only worker is the L4 engineer (with L3 review), so this is the **L4-only** eval.
L5 and L4+L5 evals come later.

## How it works

One SWE-bench **instance** = one real issue: a repo, a `base_commit`, the issue
text, and hidden tests. The eval runs in two stages.

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
Docker images. Install Docker first, then:

```bash
uv run python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path evals/swebench/out/predictions.jsonl \
  --max_workers 4 \
  --run_id langbridge-l4-run1
```

This writes a report with how many instances were **resolved** (FAIL_TO_PASS now
pass and PASS_TO_PASS still pass).

## Known limitation (legacy host runner only)

`run_eval.py` runs in a plain checkout **without the repo's dependencies
installed**, so its own `run_tests` / `execute_program` calls may fail. L4 can
still read code and produce a patch, but it cannot verify against the real test
environment. This caps quality. The Docker runner (`run_eval_docker.py`) fixes
this by running the agent inside the same per-instance image used for grading.
