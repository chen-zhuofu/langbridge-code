# Cheat sheet

LLM prompts for bench agent runs: `eval/prompt/` (e.g. task wrapper).
Dataset-pipeline prompts: `data-pipeline/prompt/`.

## Crawl task 
uv run python data-pipeline/run_pipeline.py --limit 1

## Wipe crawled task
uv run python data-pipeline/reset_task.py pytest-dev__pytest-14730

## Run against task
uv run python eval/langbridge-bench/run_eval.py --task pytest-dev__pytest-14694

uv run python eval/langbridge-bench/run_eval.py --workers 10

## Eval network guard (automatic, no setup)
Agent containers run on an internal Docker network with no direct internet.
Sole egress: the auto-started lb-eval-proxy container, which only tunnels
the LLM API host. `docker logs lb-eval-proxy` shows denied hosts.
Bypass for debugging: --open-network (results not benchmark-valid).

## Live session artifacts
While a task runs, host `artifacts/evals/<run>/<task_id>/session/` is bind-mounted
to `/root/lb_session_artifacts` in the container — progress/traces stream live.