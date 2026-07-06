# Langbridge-bench — self-built benchmark from GitHub PRs

Turns real, merged GitHub pull requests into training/eval tasks in the
**SWE-bench schema** (same fields as public SWE-bench). Each task is one bug fix:
the agent gets the issue text and the repo at the commit *before* the fix; hidden
tests decide if the patch is correct.

Pair with **`evals/swe-bench/`** (public HuggingFace SWE-bench for e2e L4 eval).

## On-disk layout (work-trial style)

```
langbridge-bench/
  instances/<task_id>.json   # raw validated instance (SWE-bench schema)
  specs/<task_id>.json       # eval-ready spec (what training runners load)
  out/                       # pipeline scratch jsonl + logs (gitignored)
  collect_prs.py             # stage 1: mine PRs from GitHub
  reference_test.py          # stages 2–4: validate with pytest F2P/P2P
  materialize.py             # jsonl → instances/*.json + specs/*.json
```

Training eval/train reads **`specs/`** by default:

```bash
uv run python -m langbridge_cli.training.cli eval --role l3 --limit 5
# same as --source langbridge-bench (swebench is a backward-compat alias)
```

### Parallel Docker eval (recommended for throughput)

The host `training.cli eval` runner is **serial**. For parallel, isolated runs
(one container per task, repo venv + pytest inside):

```bash
uv run python evals/langbridge-bench/run_eval_docker.py --role loop --workers 4 --limit 5
```

Options: `--role {loop,l4,l5,pm}`, `--workers`, `--limit`, `--model`, `--timeout`,
`--out`. Results land in `evals/langbridge-bench/out/docker_run_summary.json` and
`training/results/`.

## Pipeline

| Step | What it does | Script |
| --- | --- | --- |
| 1. Collect PRs | Merged PRs touching code **and** tests, `< 15` files, linked issue (`fixes #N`). | `collect_prs.py` |
| 2. Build env | Runnable checkout + venv (Python/pytest). | `reference_test.py --run` |
| 3. Reference test | Run tests pre-fix and post-fix. | `reference_test.py --run` |
| 4. Keep good tasks | Require ≥1 `FAIL_TO_PASS`. | `reference_test.py` |
| 5. Materialize | Split jsonl into `instances/` + `specs/`. | `materialize.py` |

```bash
# Stage 1
uv run python evals/langbridge-bench/collect_prs.py --repo pytest-dev/pytest --max-scan 80 --max-per-repo 5

# Stages 2–4 (apply-only is a cheap patch check)
uv run python evals/langbridge-bench/reference_test.py --run --timeout 400

# Stage 5: persist one JSON per task
uv run python evals/langbridge-bench/materialize.py \
  --jsonl evals/langbridge-bench/out/instances_validated.jsonl
```

Collect from several repos:

```bash
uv run python evals/langbridge-bench/collect_prs.py --repos-file evals/langbridge-bench/repos.txt
```

## Spec schema (`specs/<task_id>.json`)

```json
{
  "task_id": "pytest-dev__pytest-14639",
  "status": "ok",
  "repo": "pytest-dev/pytest",
  "base_commit": "...",
  "problem_statement": "...",
  "test_files": ["testing/test_assertion.py"],
  "test_patch": "<hidden test diff>",
  "gold_code_patch": "<gold code fix>",
  "fail_to_pass": ["testing/test_assertion.py::..."],
  "pass_to_pass": ["..."],
  "hard": false
}
```

## Scaling up

- Set `GITHUB_TOKEN` for higher GitHub API limits (60 → 5000 req/h).
- `reference_test.py --run` supports **Python/pytest** only; other stacks need
  per-repo Docker images (see `evals/swe-bench/` for the public benchmark path).

## Not yet built

- Problem-statement rewrite (LLM pass to vague-ify issue text).
- GraphQL issue linkage; Docker env generation for non-pytest repos.
