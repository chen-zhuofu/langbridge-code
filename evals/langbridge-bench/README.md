# Langbridge-bench — self-built benchmark from GitHub PRs

Turns real, merged GitHub pull requests into training/eval tasks in the
**SWE-bench schema** (same fields as public SWE-bench). Each task is a real PR fix:
the agent gets a problem statement and the repo at the commit *before* the fix;
hidden tests decide if the patch is correct. Tasks include **bug fixes** and
**feature implementations** (`task_kind: "feature"` in specs).

Pair with **`evals/swe-bench/`** (public HuggingFace SWE-bench for e2e L4 eval).

## On-disk layout (work-trial style)

```
langbridge-bench/
  instances/<task_id>.json   # raw validated instance (SWE-bench schema)
  specs/<task_id>.json       # eval-ready spec (what training runners load)
  excluded.json              # tasks filtered out (vague / not bug-fix)
  instances/excluded/        # archived instances (status=excluded)
  specs/excluded/              # archived specs (not loaded by eval)
  out/                       # pipeline scratch jsonl + logs (gitignored)
  collect_prs.py             # stage 1: mine PRs from GitHub
  reference_test.py          # stages 2–4: validate with pytest F2P/P2P
  materialize.py             # jsonl → instances/*.json + specs/*.json
```

Training eval/train reads **`specs/`** by default (**27 active** tasks — see
`excluded.json`):

```bash
uv run python -m langbridge_code.training.cli eval --role l3 --limit 5
# same as --source langbridge-bench (swebench is a backward-compat alias)
```

### Task mix (27 active)

| kind | count | notes |
| --- | --- | --- |
| bug fix | 20 | raw GitHub issue text as `problem_statement` |
| feature | 7 | rewritten `problem_statement` (`task_kind: "feature"`) |

Feature tasks use actionable requirements derived from the hidden tests, not the
original vague GitHub issue. Rewrites live in
`rewrite_feature_statements.py`; re-apply with:

```bash
uv run python evals/langbridge-bench/rewrite_feature_statements.py
```

| task_id | feature summary |
| --- | --- |
| `tqdm__tqdm-1130` | `delay` param — hide bar for fast loops |
| `tqdm__tqdm-1493` | typed `envwrap` for env-var overrides |
| `networkx__networkx-8630` | `barycenter` alias of `centroid` |
| `networkx__networkx-8591` | `bipartite.butterflies()` counting |
| `pallets__click-3473` | `help` on `Argument` + help page section |
| `pytest-dev__pytest-14568` | public `pytest.register_fixture()` |
| `pytest-dev__pytest-14576` | assert diff for `dict.items()` `>=` / `<=` |

### Still excluded (3)

Archived under `specs/excluded/` — not loaded by eval (`ok_only=True`; subdirs
not scanned).

| task_id | reason |
| --- | --- |
| `python-websockets__websockets-1543` | documentation gap, not a code bug |
| `sympy__sympy-23082` | vague ("maybe diff should…") |
| `pygments__pygments-3090` | duplicate of `pygments-3078` |

Full manifest: `excluded.json`.

### Kimi Code CLI eval (external agent)

Run the same **27 tasks** with the neighboring [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code)
(`../kimi-code`) instead of LangBridge Code's built-in workflow:

```bash
# once: install kimi (Node 18+)
curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash

# credentials (pick one):
# 1) OAuth (recommended for Kimi Code agent loop): kimi login
# 2) Moonshot API key from ~/.langbridge — the runner auto-writes
#    ~/.kimi-code/config.toml on start (no manual /login needed for config)

# smoke (1 task, goal mode, serial)
uv run python evals/langbridge-bench/run_eval_kimi_code.py --limit 1

# full bench (default: --mode goal --workers 1, timeout 3600s/task)
uv run python evals/langbridge-bench/run_eval_kimi_code.py
```

The runner reads your Moonshot key from `~/.langbridge/config.json`, writes
`~/.kimi-code/config.toml`, and invokes `kimi -p "/goal …"` per task. Use
`--model kimi-k2.7-code` to override the default model id. If `kimi` hangs on
the first LLM turn with an API-key provider, run `kimi login` once (OAuth) and
retry.

Options: `--mode {goal,prompt}`, `--model` (kimi `-m` alias), `--kimi-bin`, `--workers`,
`--timeout`, `--limit`. Results: `out/kimi_code_run_summary.json`,
`out/kimi_code_artifacts/<task_id>/`, and `training/results/l4/` (dataset
`langbridge-bench-kimi-code`).

### Parallel Docker eval (langbridge agents)

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
