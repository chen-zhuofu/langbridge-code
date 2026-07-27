# Interactive bench (SWE-Chat)

Everything for the multi-turn eval lives in this folder:
pipeline, specs, docker contexts, drops, harness, tests.

Design: [`DESIGN.md`](DESIGN.md).

## Stages

| Stage | Script | Output |
| --- | --- | --- |
| **collect** | `data-pipeline/collect/collect.py` | `data-pipeline/collect/out/sessions.jsonl` |
| **resolve** | `data-pipeline/resolve/resolve.py` | `data-pipeline/resolve/out/instances.jsonl` |
| **enrich** | `data-pipeline/enrich/enrich.py` | `data-pipeline/enrich/out/instances.jsonl` |
| **intent** | `data-pipeline/intent/analyze.py` | `data-pipeline/intent/out/instances.jsonl` |
| **env** | `data-pipeline/env/build_env.py` | `data-pipeline/env/out/` + `data/docker-images/` |
| **reference** | `data-pipeline/reference/reference_test.py` | `data-pipeline/reference/out/` (needs F2P) |
| **curate** | `data-pipeline/curate/curate.py` | `data/specs/<id>.json` |
| **eval** | `eval/run_eval.py` | `eval/out/<stamp>/` |

## Setup

1. Put SWE-Chat parquet tables somewhere:

```text
swe-chat/
  sessions.parquet
  checkpoints.parquet
  commits.parquet
  conversations.parquet
```

2. Env vars:
   - `GITHUB_TOKEN` / `GH_TOKEN` — resolve + enrich diffs
   - `OPENAI_API_KEY` (optional) — intent / sim LLM; heuristics if missing
   - Docker + base image `langbridge-bench:py312` — env/reference

## Run

Full pipeline (stops when N curated specs land, or backlog empty):

```bash
uv run python interactive-bench/data-pipeline/run_pipeline.py \
  --data-dir /path/to/swe-chat --limit 5
```

One stage:

```bash
uv run python interactive-bench/data-pipeline/collect/collect.py --data-dir /path/to/swe-chat --limit 50
uv run python interactive-bench/data-pipeline/resolve/resolve.py --data-dir /path/to/swe-chat --limit 20
uv run python interactive-bench/data-pipeline/enrich/enrich.py --data-dir /path/to/swe-chat --limit 20
uv run python interactive-bench/data-pipeline/intent/analyze.py --limit 20
uv run python interactive-bench/data-pipeline/env/build_env.py --limit 1
uv run python interactive-bench/data-pipeline/reference/reference_test.py --limit 1
uv run python interactive-bench/data-pipeline/curate/curate.py --limit 1
```

Eval smoke (stub agent, no Docker agent yet):

```bash
uv run python interactive-bench/eval/run_eval.py --stub --limit 1
```

## Tests

```bash
uv run pytest interactive-bench/tests -q
```
