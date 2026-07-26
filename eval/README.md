# Cheat sheet
## Crawl task 
uv run python data-pipeline/run_pipeline.py --limit 1

## Wipe crawled task
uv run python data-pipeline/reset_task.py pytest-dev__pytest-14730

## Run against task
uv run python eval/langbridge-bench/run_eval.py --task pytest-dev__pytest-14694

## Eval network guard (automatic, no setup)
Agent containers run on an internal Docker network with no direct internet.
Sole egress: the auto-started lb-eval-proxy container, which only tunnels
the LLM API host. `docker logs lb-eval-proxy` shows denied hosts.
Bypass for debugging: --open-network (results not benchmark-valid).