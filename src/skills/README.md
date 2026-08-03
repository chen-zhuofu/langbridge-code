# langbridge-code skills (analysis copy)

Copied from `/Users/zhuofuchen/Desktop/Repo/langbridge-code/src/skills`
for prompt review alongside [`../_full.md`](../_full.md) and
[`../subagents/`](../subagents/). **Not the live runtime** — edit here first,
apply back to `langbridge-code` later.

## Layout

| Path | What |
|---|---|
| [`langbridge/`](langbridge/) | Main agent — `LANGBRIDGE_SKILL_NAMES` |
| [`planner/`](planner/) | Planner — `PLANNER_SKILL_NAMES` |
| [`worker_coder/`](worker_coder/) | Worker (coding) — `WORKER_CODING_SKILL_NAMES` |
| [`reviewer_code/`](reviewer_code/) | Reviewer — `REVIEWER_CODING_SKILL_NAMES` |
| [`_external/`](_external/) | Upstream vendors (superpowers, mattpocock, guard-skills); not loaded by role `read_skill` unless copied/symlinked into a role dir |
| [`_catalog_init.py`](_catalog_init.py) | Snapshot of `skills/__init__.py` (role → skill name lists) |

Explorer has **no** role skills (`EXPLORER_SKILL_NAMES = ()`).

Catalog wiring (from `_catalog_init.py`):

```text
langbridge:   grilling, writing-simple-plans, superpowers_systematic-debugging
              (+ draft langbridge_brainstorming)
planner:      (none — plan format lives in subagents/planner.md)
worker_coder: superpowers_test-driven-development, superpowers_systematic-debugging
reviewer_code: clean-code-guard, test-guard, docs-guard
```

Role-scoped `read_skill` only searches `skills/<role>/`.

## Role skills (loadable)

### langbridge (main)

- [`grilling`](langbridge/grilling/SKILL.md) — stress-test plan/design before coding; grill / grill me
- [`langbridge_brainstorming`](langbridge/langbridge_brainstorming/SKILL.md) — **draft** adapted design dialogue (not wired into catalog / `_full.md` yet)
- [`writing-simple-plans`](langbridge/writing-simple-plans/SKILL.md) — obvious multi-step plan → write `todo_list.md` yourself
- [`superpowers_systematic-debugging`](langbridge/superpowers_systematic-debugging/SKILL.md) — bug / test failure before proposing fixes

### planner

No role skills. Decomposition / `todo_list` contracts are in
[`../subagents/planner.md`](../subagents/planner.md). Upstream mirrors remain
under [`_external/superpowers/`](_external/superpowers/) for comparison only.

### worker_coder

- [`superpowers_test-driven-development`](worker_coder/superpowers_test-driven-development/SKILL.md)
- [`superpowers_systematic-debugging`](worker_coder/superpowers_systematic-debugging/SKILL.md)

### reviewer_code

- [`clean-code-guard`](reviewer_code/clean-code-guard/SKILL.md) (+ `references/`)
- [`test-guard`](reviewer_code/test-guard/SKILL.md)
- [`docs-guard`](reviewer_code/docs-guard/SKILL.md)
## Upstream (`_external/`)

| Pack | Role |
|---|---|
| [`_external/superpowers/`](_external/superpowers/) | Upstream Superpowers (brainstorming, writing-plans, TDD, debugging, …) |
| [`_external/mattpocock-skills/`](_external/mattpocock-skills/) | grill-me / grilling sources |
| [`_external/guard-skills/`](_external/guard-skills/) | Guard skill sources |

Compare role copies vs `_external` when deciding what to keep, slim, or drop
(e.g. main `langbridge_brainstorming` / `grilling` vs upstream brainstorming).
