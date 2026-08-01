# Interactive bench (SWE-Chat → LangBridge)

Self-contained multi-turn coding-agent eval under ``interactive-bench/``.
Does **not** reuse SWE-Together's 109 tasks as primary source; may reference
their ideas. Existing PR-based ``data-pipeline/`` + ``langbridge-bench`` stay
untouched.

## Unit & admission

| Rule | Decision |
| --- | --- |
| Unit | 1 session = 1 task (same topic). Drop Mind Changer / multi-topic. |
| Tests | Must have runnable tests. No valid F2P → drop. |
| Merge | Work must be reachable from GitHub **default branch** (admission filter). |
| Environment | Same pattern as langbridge-bench: per-task `lb-interactive:<id>` Docker. |

## Commits

```
session → sessions.checkpoint_ids
       → checkpoints.commit_shas (all)
       → commits (by commit_date)
```

| Field | Definition |
| --- | --- |
| Candidate commits | Union of `commit_shas` across all `checkpoint_ids`. |
| First / last (table) | Earliest / latest by `commit_date` among candidates. |
| **gold** | Among candidates, the SHA that is an ancestor of default-branch HEAD. If several, latest by date. If none → drop. |
| **base** | Git parent of the earliest commit on the chain to gold. If unresolved → drop. |
| Session diff | `git diff base gold`. Cross-check `sessions.files_touched`; dirty → drop. |

One checkpoint may list multiple SHAs (amend/rebase copies). Prefer reachable SHA.

## Simulator

Actions: `no-op`, `steer`, `reveal_next`, `answer`. No `check_external`.

| Action | Effect |
| --- | --- |
| `no-op` | Inject `"continue"`. Not counted as user intervention. |
| `steer` / `reveal_next` / `answer` | Inject sim text. Count as user input. |

Reveal is state-conditioned. Turn 0 = first user message verbatim.

## Stop

| Condition | Behavior |
| --- | --- |
| Consecutive `no-op` | Cap **4**. |
| Timeout | ``max(40 minutes, original agent runtime × 2)``. |
| Sim / intent judge | Must **not** end the episode. |

## Scoring (vs original session)

1. Correctness: tests + intent coverage.
2. Intervention cost: real sim openings (not `"continue"`).
3. Time: agent runtime vs original.
4. Steps: # of real user inputs (exclude `"continue"`).

## Labels

- `task_type`: `bug_fix` / `feature` / `refactor` / `other`
- `difficulty`: bucket by gold-patch complexity — worst of files changed (easy ≤2, medium ≤5,
  else hard), LOC changed (≤30 / ≤120 / else), FAIL_TO_PASS count (≤2 / ≤5 / else). Computed at
  curate (F2P isn't known until reference).
- `horizon`: bucket by original agent runtime — how long-horizon the task is (short ≤10m,
  medium ≤30m, else long). Computed at enrich.

## Layout

```
interactive-bench/
  DESIGN.md
  README.md
  data-pipeline/    # collect → … → curate
    run_pipeline.py
    collect/
    resolve/
    enrich/
    intent/
    env/
    reference/
    curate/
    _lib/
  harness/          # sim loop + scoring
  eval/             # eval runner (stub agent supported)
  tests/
  scripts/
  data/             # specs, docker-images, drop
```

## Pipeline stages

`collect` → `resolve` → `enrich` → `env` → `reference` → `curate`

``curate`` runs after ``reference`` so LLM intent extraction only hits tasks
that already have valid FAIL_TO_PASS. Poor / empty intents are dropped there
(along with oracle / F2P quality gates).

Then: `eval/run_eval.py` (sim harness).

All outputs stay under ``interactive-bench/``.
