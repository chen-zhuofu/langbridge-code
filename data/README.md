# Eval artifacts

```
data/
  eval/
    specs/                 # real dir (NOT symlink); eval reads this
      <task_id>.json       # synced from curate/out (skip if exists / human-dropped)
    drop/                  # HUMAN error analysis
      drop_task.py
      drop.json
      specs/<id>.json
      docker-images/<id>/
    docker-images/<id>/    # 1:1 with eval specs after curate prune
    legacy/                # archive; code ignores
```

Curate writes `data-pipeline/curate/out/`, then **copies** into `data/eval/specs/`
unless the id is already in specs or listed in `data/eval/drop/drop.json`.

Pipeline: [`data-pipeline/README.md`](../data-pipeline/README.md).
