# Cheat sheet

Layout:

```
eval/
  run_eval.py          # internal langbridge-bench (agent + in-container grade)
  run_public_eval.py   # SWE-bench verified / pro (official images + harness)
  run_agent.py         # in-container agent entry
  grade_checkout.py    # in-container grade entry
  util/                # eval helpers (bench, metrics, telemetry, …)
  sandbox/             # Docker / network / agent venv
  prompt/              # agent task prompts
  data/
    data-pipeline/     # collect → env → reference → curate
    langbridge-bench/  # curated specs + docker-images
    public/            # verified / pro (official data + images only)
```

## Crawl task
```bash
uv run python eval/data/data-pipeline/run_pipeline.py --limit 1
```

## Wipe crawled task
```bash
uv run python eval/data/data-pipeline/reset_task.py pytest-dev__pytest-14730
```

## Own tasks (langbridge-bench): agent + grade in the same Docker image
```bash
uv run python eval/run_eval.py --task pytest-dev__pytest-14694
uv run python eval/run_eval.py --workers 10
# default bench dir is eval/data/langbridge-bench
```

## SWE-bench Verified / Pro

Agent runs **inside the official instance image**. Bootstrap installs a
portable Python 3.12 under `/opt/lb-venv` via uv and does **not** put that
venv on PATH (so bash `python`/`pytest` stay on the image toolchain).
Scoring uses the **official** harness, not our in-container grader.

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
# Stage 2 — Scale harness: https://github.com/scaleapi/SWE-bench_Pro-os
```

## Eval network guard (langbridge-bench runner only)
Agent containers on `eval/run_eval.py` run on an internal Docker network with
no direct internet. Sole egress: auto-started `lb-eval-proxy` (LLM API host
only). Bypass for debugging: `--open-network` (results not benchmark-valid).

`run_public_eval.py` currently uses the default Docker network (needed for uv
bootstrap + LLM). Treat those runs as open-network unless you add a guard later.

## Live session artifacts
While a langbridge-bench task runs, host `artifacts/evals/<run>/<task_id>/session/`
is bind-mounted to `/root/lb_session_artifacts` in the container.
