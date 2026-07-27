# Dataset pipeline

LLM prompts live in `data-pipeline/prompt/` (curate keep/rewrite/drop,
classify). Eval/bench prompts live in `eval/prompt/`.

**Multi-turn interactive bench** lives in a separate top-level tree:
[`interactive-bench/`](../interactive-bench/) (not part of this PR pipeline).

Flow:

`collect` → `env` → `reference` → `curate` → `eval`

**Invariant (each stage):** `own_output ∪ own_drop` = previous stage’s output  
(for ids that stage has decided). Resume only reads **own** out + **own** drop.

| Stage | Script | In | Out | Drop |
| --- | --- | --- | --- | --- |
| **1 collect** | `collect/collect.py` | `collect/in/repos.md` | `collect/out/instances.jsonl` | — (resume = own out only) |
| **2 env** | `env/build_env.py` | collect jsonl | `env/out/instances.jsonl` + `docker-images/` + `lb-task:<id>` | `env/out/drop.json` |
| **3 reference** | `reference/reference_test.py` | env jsonl | `reference/out/instances.jsonl` (incl. F2P/P2P) | `reference/out/drop.json` |
| **4 curate** | `curate/curate.py` | reference jsonl | keep / salvage-rewrite / drop → LLM `task_type`/`difficulty` (+ reasons, uses F2P) → `curate/out/`; sync **copy** → `data/langbridge-bench/specs/`; prune docker-images | `curate/out/drop.json` (LLM) |

Curate: **keep** good statements; **rewrite** only to salvage unclear ones by
filling behavior from hidden tests (no solution leak); otherwise **drop**.
Also **drop** when the statement says repro needs another repo/checkout outside
the task repo (e.g. clone Home Assistant) — do not rewrite around that.

**Drop broken problem manually:**
```bash
uv run python data/langbridge-bench/drop/drop_task.py pytest-dev__pytest-14694 --reason "$(cat <<'EOF'
problem statement too vague to reproduce the hidden-test case (rootdir / conftest visibility). Agent repros always pass, so it cannot locate the real bug and drifts to unrelated failures.
EOF
)"
```

**Wipe a task as if never collected** (pipeline outs + specs + docker-images + tag + human drop archive):
```bash
uv run python data-pipeline/reset_task.py pytest-dev__pytest-14730
```

```bash
uv run python eval/run_eval.py --task pytest-dev__pytest-14730
```

**Produce N new benches end-to-end:**

```bash
uv run python data-pipeline/run_pipeline.py --limit 1
```

`--limit N` means keep running until **N new** files appear under `data/langbridge-bench/specs/`
(or the pipeline is stuck). Failed / dropped attempts do not count. The runner
drains `curate` → `reference` → `env` backlog before collecting more.
