# Bench artifacts

```
data/
  langbridge-bench/          # self-built PR tasks
    specs/                   # real dir; eval reads this
      <task_id>.json
    drop/                    # HUMAN error analysis
      drop_task.py
      drop.json
      specs/<id>.json
      docker-images/<id>/
    docker-images/<id>/
    _legacy/                 # archive; code ignores
  swe-bench/                 # SWE-bench imported into same spec format
    lite|verified|pro/
      specs/
      docker-images/
      drop/
  swe-chat/                  # local SWE-Chat parquet downloads (gitignored)
```

Curate writes `data-pipeline/curate/out/`, then **copies** into `data/langbridge-bench/specs/`
unless the id is already in specs or listed in `data/langbridge-bench/drop/drop.json`.

Eval:

```bash
uv run python eval/run_eval.py --bench-dir data/langbridge-bench
uv run python eval/run_eval.py --bench-dir data/swe-bench/lite --limit 10
```

Pipeline: [`data-pipeline/README.md`](../data-pipeline/README.md).
