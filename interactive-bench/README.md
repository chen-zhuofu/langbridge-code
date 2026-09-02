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
| **env** | `data-pipeline/env/build_env.py` | `data-pipeline/env/out/` + `data/docker-images/` |
| **reference** | `data-pipeline/reference/reference_test.py` | `data-pipeline/reference/out/` (needs F2P) |
| **curate** | `data-pipeline/curate/curate.py` | `data/specs/<id>.json` (intent LLM + gates) |
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
   - `OPENAI_API_KEY` / LangBridge `~/.langbridge/config.json` `api_keys`
     (deepseek / moonshot / openai / anthropic) — intent / sim LLM
   - Models: intent extraction defaults to `claude-fable-5` (pipeline-only).
     Eval sim / coverage defaults live in `eval/config.json` → `interactive`
     (`gpt-5.6` / `claude-fable-5`). Override with `LB_INTENT_MODEL` /
     `LB_SIM_MODEL` / `LB_COVERAGE_MODEL`, or `LB_INTERACTIVE_MODEL` for all.
   - Intent coverage is reference-only for now: it is LLM-judged on real runs
     (falls back to the revealed-intents proxy if the judge is unreachable)
     and does not gate pass/fail — `pass` = valid episode + F2P tests green.
     TODO: once the judge is validated as accurate, promote coverage to a
     hard pass criterion.
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
uv run python interactive-bench/data-pipeline/env/build_env.py --limit 1
uv run python interactive-bench/data-pipeline/reference/reference_test.py --limit 1
uv run python interactive-bench/data-pipeline/curate/curate.py --data-dir /path/to/swe-chat --limit 1
# optional debug: intent LLM alone (normally runs inside curate)
# uv run python interactive-bench/data-pipeline/intent/analyze.py --limit 20
```
Eval (same CLI shape as langbridge-bench: `--workers` / `--offset` / `--limit` / `--task`).
Sim and coverage judge run on the **host**. The coding agent runs in
`lb-interactive:<id>`; after the episode the runner captures `candidate.diff`,
tears down the agent container, then grades F2P in a **fresh** same-image
container (sequential dual Docker). The coding agent under test defaults to
DeepSeek (`eval/config.json`); sim and intent-coverage judge models live under
`interactive` (`sim_model` / `coverage_model`). Env overrides: `LB_SIM_MODEL`,
`LB_COVERAGE_MODEL`, or `LB_INTERACTIVE_MODEL` for both. The resolved models are
written into `eval/out/<stamp>/report.json` as `config`.

```bash
uv run python interactive-bench/eval/run_eval.py --stub --limit 1
uv run python interactive-bench/eval/run_eval.py --workers 2 --offset 1 --limit 2
uv run python interactive-bench/eval/run_eval.py --task <id>
```

## Tests

```bash
uv run pytest interactive-bench/tests -q
```
