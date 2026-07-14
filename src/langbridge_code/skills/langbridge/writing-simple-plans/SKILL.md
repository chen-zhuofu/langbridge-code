---
name: writing-simple-plans
description: Use when multi-step work needs a committed todo_list but the plan is already obvious — write it yourself with update_plan instead of dispatching agent_planner.
---

## LangBridge Code mapping (main agent)

This is the self-planning playbook for your middle triage tier: multi-step work
whose plan is obvious. Light work needs no plan at all. Heavy planning
(research, trade-offs, decomposition) goes to agent_planner — do not do it
yourself. Commit the plan with update_plan before dispatching any agent_worker.

# Writing Simple Plans

## The obviousness test

The plan is "obvious" only if you can already name, without further research:
- the files each todo touches,
- the order and dependencies between todos,
- an exact verify command for each coding todo.

If any of these needs exploration or a design decision, stop — dispatch
agent_explorer for the missing facts or agent_planner for the plan.

## Format

update_plan expects the full session plan markdown. Keep every section short,
but include them all:

```markdown
<!-- task_type: coding -->
# Plan: <short name>

## Desired end state
<one or two sentences — what "done" looks like and how to verify the whole thing>

## Success criteria
- Automated: <exact command, e.g. pytest tests/foo/test_bar.py -v>

## Out of scope
- <what we are NOT doing>

## Todo list
- [ ] Add X to src/pkg/mod.py <!-- depends: none --> <!-- verify: pytest tests/test_mod.py -v -->
- [ ] Update Y in src/pkg/other.py <!-- depends: none --> <!-- verify: pytest tests/test_other.py -v -->
- [ ] Wire X into Y <!-- depends: 1, 2 --> <!-- verify: pytest tests/ -v -->
- [ ] Verify merged codebase and run integration tests <!-- depends: 3 --> <!-- integration -->
```

Rules:
- Every todo carries `<!-- depends: none -->` or `<!-- depends: 1, 2 -->`
  (1-based, top→bottom). Ready todos dispatch in parallel — give independent
  todos `depends: none` and non-overlapping files.
- Every coding todo carries `<!-- verify: <exact command> -->`.
- With 3+ implementation todos, end with one `<!-- integration -->` verification
  todo that depends on the last of them.

## Task right-sizing

One todo = one reviewable deliverable. Fold setup, config, and doc tweaks into
the todo whose deliverable needs them. Split only where a reviewer could reject
one todo while approving its neighbor. Never cut one coherent change (a function
and its test, or tightly coupled edits) into fragments that only make sense
together.

## No placeholders

Exact file paths and exact commands only. "Add validation", "handle edge cases",
"write tests for the above", and TBD are plan failures — if you cannot state the
concrete edit or check, the plan is not obvious; use agent_planner.
