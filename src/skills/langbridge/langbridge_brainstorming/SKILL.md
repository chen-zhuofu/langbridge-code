---
name: langbridge_brainstorming
description: >-
  Turn a fuzzy feature or product idea into an approved design before planning
  or coding. Use when scope, approach, or product shape is still open — not for
  light well-scoped edits, not for stress-testing an already-formed plan (use
  grilling), and not when Ambiguity gate alone can settle one blocking choice.
---

## LangBridge Code mapping (main agent)

Run this yourself on the main agent. Ask via `ask_user` only — one question per
call. Prefer tools / `agent_explorer` for facts over asking. Do not start
`agent_planner`, write `todo_list.md`, or dispatch `agent_worker` until the user
approves the design (short chat approval is enough; a written spec is optional).

After approval: if the implementation plan is obvious, use `writing-simple-plans`;
if drafting is heavy, dispatch `agent_planner`. Pass any approved design path in
worker `supplemental_context` when you later dispatch.

This skill does **not** replace Triage light-work (do it yourself, no plan) or
the Ambiguity gate (one blocking product fork → ask and continue).

# Brainstorming — ideas into designs

Help turn ideas into a clear design through short collaborative dialogue. Goal:
an approved answer to *what* we are building and *which approach*, not a
step-by-step `todo_list.md` (that comes next).

## When to use / skip

**Use when** the user wants a new feature, behavior change, or product shape and
you cannot yet name the intended deliverable, primary approach, and key
boundaries without guessing.

**Skip when**
- Light, well-scoped work (typo, one-liner, obvious small edit)
- Plan/steps are the only open work — go to plan / `writing-simple-plans` /
  `agent_planner`
- User asks to stress-test an existing plan or says grill → `grilling`
- Only one consequential fork remains → Ambiguity gate + one `ask_user`, not
  this whole playbook

## Process

Do these in order. Scale depth to complexity — a tiny feature may be a few
sentences of design; a large one gets sections.

1. **Orient** — skim relevant files, docs, recent commits (or dispatch
   `agent_explorer` if the tree is large). Follow existing patterns.
2. **Scope check** — if the request packs several independent subsystems, say so
   and help pick the first slice. Do not refine details of a mega-project that
   still needs splitting.
3. **Clarify** — one `ask_user` at a time. Focus on purpose, constraints, success
   criteria, and decisions only you cannot look up. Prefer concrete choices;
   offer a recommended answer each time.
4. **Approaches** — propose 2–3 options with trade-offs; lead with your
   recommendation. Get a pick (or a hybrid the user states).
5. **Present design** — cover, as needed: shape/architecture, main pieces,
   data/control flow, error handling, testing expectations, and explicit out of
   scope. Ask for approval. Revise if they push back.
6. **Optional written spec** — only if the design is large enough that workers
   will need a stable reference, or the user asks. Write a concise markdown file
   where the user prefers (default suggestion: under the session artifacts or a
   path they name). No required `docs/superpowers/...` path. Do not commit unless
   the user wants a commit.
7. **Hand off to planning** — after approval, write or dispatch the
   implementation plan (`writing-simple-plans` or `agent_planner`). Do not jump
   straight to `agent_worker`.

## Design quality bar

- YAGNI: cut nice-to-haves that are not needed for the stated goal.
- Prefer small units with clear interfaces over sprawling files.
- In existing codebases, improve boundaries only when it serves this goal — no
  unrelated refactors.
- No placeholders in the approved design ("TBD", "figure out later") for
  decisions that would block planning.

## Versus sibling skills

| Need | Skill |
|---|---|
| Block on one product fork | Ambiguity gate (system) |
| Stress-test a formed plan/idea | `grilling` |
| Form design/spec from a fuzzy idea | **this skill** |
| Obvious multi-step todos | `writing-simple-plans` |
| Heavy plan draft | `agent_planner` |
