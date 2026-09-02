# Cheat sheet

Layout:

```
eval/
  run_eval.py          # langbridge-bench (agent container → patch → grade container)
  run_public_eval.py   # SWE-bench verified / pro (official images + harness)
  run_agent.py         # in-container agent entry
  grade_checkout.py    # in-container grade entry
  util/                # eval helpers (bench, metrics, telemetry, …)
  sandbox/             # Docker / network / agent venv
  prompt/              # agent task prompts
  data-pipeline/       # collect → env → reference → curate
  data/
    langbridge-bench/  # curated specs + docker-images
    public/            # verified / pro (official data + images only)
```
## Manual test 
```bash
uv run --project ~/langbridge langbridge
```

## Crawl task
```bash
uv run python eval/data-pipeline/run_pipeline.py --limit 1
```

## Wipe crawled task
```bash
uv run python eval/data-pipeline/reset_task.py pytest-dev__pytest-14730
```

## Own tasks (langbridge-bench): sequential dual containers

Same task image (`lb-task:<id>`), two containers in sequence:

1. **Agent** container (optional egress guard) → `candidate.diff`
2. Tear down agent
3. **Grade** container (fresh same image, no egress) → `grade.json`

```bash
uv run python eval/run_eval.py --task pytest-dev__pytest-14694
uv run python eval/run_eval.py --workers 10
# default bench dir is eval/data/langbridge-bench
```

Agent under test defaults to the DeepSeek stack in `eval/config.json`
(`deepseek-v4-pro` + explorer `deepseek-v4-flash`), independent of your
interactive CLI provider. Override with `--model` / `LANGBRIDGE_API_PROVIDER`.

## SWE-bench Verified / Pro

Also sequential dual containers, harness-owned grade:

1. Agent runs **inside the official instance image**
2. Official harness (Scale / swebench) starts **its own** grade containers

Bootstrap installs a portable Python 3.12 under `/opt/lb-venv` via uv and does
**not** put that venv on PATH (so bash `python`/`pytest` stay on the image
toolchain). Scoring uses the **official** harness, not our in-container grader.

Lite is dropped. Adapted LangBridge specs for public benches are not used.

```bash
# Stage 1 — generate predictions (verified)
uv run python eval/run_public_eval.py --difficulty verified --count 10

# Stage 2 — official grade (verified)
cd eval && uv run python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path out/predictions.jsonl \
  --max_workers 4 --run_id langbridge-verified
```

```bash
# Stage 1 — pro (repo at /app; images from jefzda/sweap-images)
uv run python eval/run_public_eval.py --difficulty pro --count 10

# Stage 2 — unchanged Scale harness, launched with stable local resources
uv run python eval/run_pro_grader.py \
  --grader-script /path/to/SWE-bench_Pro-os/swe_bench_pro_eval.py \
  --raw_sample_path /path/to/sample.jsonl \
  --patch_path eval/out/predictions-pro.json \
  --output_dir eval/out/official-grade \
  --dockerhub_username jefzda \
  --scripts_dir /path/to/SWE-bench_Pro-os/scripts/run_scripts \
  --use_local_docker --block_network
```

For Pro, the runner mirrors Scale's task template exactly: `problem_statement`,
`requirements`, and `interface` are concatenated without normalization. The run
summary records the protocol version and each prompt's SHA256; checkpoints from
an older or mismatched protocol are not resumed. The per-instance harness timeout
defaults to 7,200 seconds (two hours). Pro defaults to two concurrent containers.
Go instances additionally use `--cpus 2`, `GOFLAGS=-p=1`, and
`GOMAXPROCS=2`. On a hard timeout the runner stops all container writers before
capturing the canonical checkout's partial patch. `run_pro_grader.py` keeps the
upstream Scale test and pass/fail logic intact while applying the same two-CPU
profile and capping local grader concurrency at two.

## Eval network guard (langbridge-bench runner only)
Agent containers on `eval/run_eval.py` run on an internal Docker network with
no direct internet. Sole egress: auto-started `lb-eval-proxy` (LLM API host
only). Bypass for debugging: `--open-network` (results not benchmark-valid).

`run_public_eval.py` currently uses the default Docker network (needed for uv
bootstrap + LLM). Treat those runs as open-network unless you add a guard later.

## Live session artifacts
While a langbridge-bench task runs, host `artifacts/evals/<run>/<task_id>/session/`
is bind-mounted to `/root/lb_session_artifacts` in the container.
