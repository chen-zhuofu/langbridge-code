---
name: creating-skills
description: Interactively design and create or revise a personal LangBridge skill through one-question-at-a-time discovery, shared-understanding confirmation, implementation, and validation. Use when the user asks to create a skill, wants guided help turning a workflow into a skill, or has an incomplete skill idea that must be clarified before files are written.
---

# Create Skills Interactively

Own the conversation and the implementation. Ask only for decisions; inspect the
workspace or existing skills to answer discoverable factual questions yourself.

## Discover

1. Ask exactly one focused question at a time and wait for the answer.
2. Include a recommended answer with each question and briefly explain the tradeoff.
3. Give a rough count of unresolved questions when useful; revise it as new branches appear.
4. Cover only unresolved parts of this design:
   - intended outcome and representative requests that should trigger the skill;
   - requests that must not trigger it and other scope boundaries;
   - repeatable workflow, required tools, and reusable scripts, references, or assets;
   - whether an existing personal skill should be revised instead of adding a new one;
   - concrete checks that will prove the skill works.
5. Do not ask for information the user already supplied.

## Confirm Shared Understanding

Before writing files, summarize the proposed:

- skill name and destination;
- trigger description and non-triggers;
- workflow and bundled resources;
- validation examples.

Ask the user to confirm or correct this shared understanding. Do not create or
edit the skill until the user explicitly confirms it.

## Build

After confirmation:

1. Store the skill under
   `~/Library/Application Support/LangBridge/skills/langbridge/<skill-name>/`.
2. For a revision, read the existing skill first and preserve compatible behavior.
3. Use a lowercase, hyphenated, preferably verb-led name of at most 64 characters.
4. Put only `name` and `description` in `SKILL.md` frontmatter. Make the
   description state both what the skill does and when it should trigger.
5. Write concise imperative instructions. Assume LangBridge is capable; include
   only non-obvious workflow knowledge and guardrails.
6. Add `scripts/`, `references/`, or `assets/` only when they are reusable. Do
   not add a README, changelog, installation guide, or other auxiliary files.
7. Test every added script with a representative input.

## Validate and Hand Off

1. Re-read the completed files and verify frontmatter, folder naming, trigger
   specificity, references, and the agreed boundaries.
2. Load the skill with `read_skill(<skill-name>)` when available to confirm
   LangBridge can discover it.
3. Walk through at least one agreed trigger and one non-trigger. Fix any mismatch
   before declaring success.
4. Report the absolute skill path, files created or changed, and validation evidence.
