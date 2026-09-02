---
name: writing-app-skills
description: Create or revise reusable, app-level LangBridge skills after a workflow has been concretely validated. Use when the user asks to capture, summarize, write, or update a Skill for the main Agent, or when a repeatable workflow learned during a task is worth proposing as a persistent personal Skill.
---

# Write App Skills

Store personal main-Agent skills under:

```text
~/Library/Application Support/LangBridge/skills/langbridge/<skill-name>/SKILL.md
```

Use the existing `read_file`, `write`, and `Edit` tools. Do not look for or call a dedicated Skill-writing tool.

## Workflow

1. Confirm that the workflow is reusable and supported by concrete successful checks. Do not capture guesses, unfinished work, secrets, one-off facts, or ordinary user preferences as a Skill.
2. Before writing, tell the user the proposed name, trigger, scope, and important instructions. Wait for explicit approval because an app-level Skill affects every project handled by the main Agent.
3. Choose a lowercase hyphenated name of at most 64 characters. Prefer a short verb-led name.
4. For an existing Skill, load it first with `read_skill` or `read_file`. Never overwrite or materially broaden it without explicit user approval.
5. Write a concise `SKILL.md` containing only `name` and `description` in YAML frontmatter. Put all trigger conditions in `description`; write the body as imperative workflow instructions.
6. Keep the body under 500 lines. Add `scripts/`, `references/`, or `assets/` only when they provide reusable value; do not add README, changelog, or installation files.
7. Load the completed Skill with `read_skill(<skill-name>)` and verify its contents. Report the absolute path and the validation evidence.

The new Skill can be loaded immediately by name and appears automatically in the main Agent's Skill catalog for new tasks. Do not place it in a project's `.langbridge/skills/` directory and do not expose it to Planner, Worker, Explorer, or Reviewer roles.
