# Cheat sheet

LLM prompts for bench agent runs: `eval/prompt/` (e.g. task wrapper).
Dataset-pipeline prompts: `data-pipeline/prompt/`.

Shared library: `eval/langbridge_eval/` (agent run + grade helpers).

## Crawl task
```bash
uv run python data-pipeline/run_pipeline.py --limit 1
```

## Wipe crawled task
```bash
uv run python data-pipeline/reset_task.py pytest-dev__pytest-14730
```

## Run against langbridge-bench
```bash
uv run python eval/run_eval.py --task pytest-dev__pytest-14694
uv run python eval/run_eval.py --workers 10
# explicit (default):
uv run python eval/run_eval.py --bench-dir data/langbridge-bench --workers 10
```

## Run against SWE-bench (same runner, same spec format)
```bash
uv run python eval/import_to_langbridge.py --difficulty lite --prefer-local --pull --count 10
uv run python eval/run_eval.py --bench-dir data/swe-bench/lite --limit 10 --workers 2 --open-network
```

## Eval network guard (automatic, no setup)
Agent containers run on an internal Docker network with no direct internet.
Sole egress: the auto-started lb-eval-proxy container, which only tunnels
the LLM API host. `docker logs lb-eval-proxy` shows denied hosts.
Bypass for debugging: `--open-network` (results not benchmark-valid).

## Live session artifacts
While a task runs, host `artifacts/evals/<run>/<task_id>/session/` is bind-mounted
to `/root/lb_session_artifacts` in the container — progress/traces stream live.

## Optional native SWE-bench runners
Legacy host/Docker paths that talk to the official swebench grader (not the
langbridge spec format): `eval/run_swe_host.py`, `eval/run_swe_docker.py`.
