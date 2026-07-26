# Dataset pipeline

Flow:

`collect` → `env` → `reference` → `curate` → `eval`

**Invariant (each stage):** `own_output ∪ own_drop` = previous stage’s output  
(for ids that stage has decided). Resume only reads **own** out + **own** drop.

| Stage | Script | In | Out | Drop |
| --- | --- | --- | --- | --- |
| **1 collect** | `collect/collect.py` | `collect/in/repos.md` | `collect/out/instances.jsonl` | — (resume = own out only) |
| **2 env** | `env/build_env.py` | collect jsonl | `env/out/instances.jsonl` + `docker-images/` + `lb-task:<id>` | `env/out/drop.json` |
| **3 reference** | `reference/reference_test.py` | env jsonl | `reference/out/instances.jsonl` (incl. F2P/P2P) | `reference/out/drop.json` |
| **4 curate** | `curate/curate.py` | reference jsonl | write `curate/out/`; sync **copy** → `data/eval/specs/` (skip if specs already has it, or id in human `drop/drop.json`); prune docker-images to match specs | `curate/out/drop.json` (LLM) |

**Drop broken problem manually:**
```bash
uv run python data/eval/drop/drop_task.py pytest-dev__pytest-14694 --reason "$(cat <<'EOF'
problem statement too vague to reproduce the hidden-test case (rootdir / conftest visibility). Agent repros always pass, so it cannot locate the real bug and drifts to unrelated failures.
EOF
)"
```

**Wipe a task as if never collected** (pipeline outs + specs + docker-images + tag + human drop archive):
```bash
uv run python data-pipeline/reset_task.py pytest-dev__pytest-14730
```

```bash
uv run python eval/langbridge-bench/run_eval.py --task pytest-dev__pytest-14730
```
