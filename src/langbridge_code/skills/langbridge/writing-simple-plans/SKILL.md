---
name: writing-simple-plans
description: Use when multi-step work needs a todo_list.md but the plan is already obvious — write the file yourself instead of dispatching agent_planner.
---

## LangBridge Code mapping (main agent)

This is the self-planning playbook for your middle triage tier: multi-step work
whose plan is obvious. Light work needs no plan at all. Heavy planning
(research, trade-offs, decomposition) goes to agent_planner — do not do it
yourself. Write the plan to the session-artifact virtual path `todo_list.md`
with the write tool; never leave a copy in the workspace root
before dispatching any agent_worker.

# Writing Simple Plans

## The obviousness test

The plan is "obvious" only if you can already name, without further research:
- the files each todo touches,
- the order and dependencies between todos,
- every required behavior and task boundary,
- observable pass/fail acceptance criteria,
- an exact verify command for each coding todo.

If any of these needs exploration or a design decision, stop — dispatch
agent_explorer for the missing facts or agent_planner for the plan.

## Format

`todo_list.md` holds the full session plan markdown. Keep every section short,
but include them all:

```markdown
# Plan: <short name>

## Desired end state
<one or two sentences — what "done" looks like and how to verify the whole thing>

## Success criteria
- Automated: <exact command, e.g. pytest tests/foo/test_bar.py -v>

## Out of scope
- <what we are NOT doing>

## Todo list
- [ ] Task 1: Add X to src/pkg/mod.py (deps: none)
  - Objective: <specific outcome>
  - Detailed requirements:
    - <required behavior or constraint>
  - Acceptance spec:
    - <observable binary pass/fail criterion>
  - Deliverables: `src/pkg/mod.py`, `tests/test_mod.py`
  - Verify: `pytest tests/test_mod.py -v`
  - Out of scope: <task-local exclusions>

- [ ] Task 2: Wire X into Y (deps: task 1)
  - Objective: ...
  - Detailed requirements: ...
  - Acceptance spec: ...
  - Deliverables: ...
  - Verify: ...
  - Out of scope: ...
```

Rules:
- Each checkbox begins one complete task contract. Never shorten it by dropping
  requirements, acceptance criteria, deliverables, verification, or boundaries.
- Acceptance spec defines observable correct behavior. Verify names the exact
  evidence that proves it; a command alone is not an acceptance criterion.
- Check every task for contradictory clauses. Ask the user instead of making a
  worker guess when code and requirements cannot resolve a product decision.
- You own every todo's status: mark its checkbox `[x]` yourself (Edit) when
  its worker passes review.
- Every todo ends with an explicit deps note: `(deps: none)` or
  `(deps: tasks N, M)`. Only `deps: none` todos may be dispatched in parallel —
  give them non-overlapping files. Todos touching the same file are almost
  never `deps: none` together.
- Every coding todo carries exact Verify commands and any necessary manual check.
- With 3+ implementation todos, end with one integration verification todo that
  comes after the last of them.

## Task right-sizing

One todo = one reviewable deliverable. Fold setup, config, and doc tweaks into
the todo whose deliverable needs them. Split only where a reviewer could reject
one todo while approving its neighbor. Never cut one coherent change (a function
and its test, or tightly coupled edits) into fragments that only make sense
together.

## No placeholders

Exact file paths, requirements, acceptance criteria, and commands only. "Add
validation", "handle edge cases", "write tests for the above", "works correctly",
and TBD are plan failures — if you cannot state the concrete behavior and check,
the plan is not obvious; use agent_planner.
