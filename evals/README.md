# evals/

Two parallel benchmarks live here:

| Directory | What it is |
| --- | --- |
| **`swe-bench/`** | Public [SWE-bench](https://www.swebench.com/) (HuggingFace Lite / Verified / Pro). End-to-end L4 eval: generate predictions, then grade with the official Docker harness. See `swe-bench/README.md`. |
| **`langbridge-bench/`** | Self-built benchmark from real merged GitHub PRs (pytest pipeline). One JSON per task under `instances/`, eval-ready specs under `specs/`. Used by `training` eval/train (`--source langbridge-bench`). See `langbridge-bench/README.md`. |

Quick start:

```bash
# Public SWE-bench (Docker runner)
sg docker -c "uv run python evals/swe-bench/run_eval_docker.py --difficulty lite --count 10"

# Self-built langbridge-bench (L3/L4/loop training eval)
uv run python -m langbridge_code.training.cli eval --role l3 --limit 5
```
