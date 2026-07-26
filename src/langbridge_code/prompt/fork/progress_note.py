"""Fork instructions for progress note-writers (session + per-task).

Bookend style mirrors Claude Code's compact NO_TOOLS_PREAMBLE / TRAILER:
the fork keeps the parent's tool schemas for prompt-cache key match, so the
model can still see tools — the prompt must hard-block using them.
"""

# Adapted from Claude Code src/services/compact/prompt.ts NO_TOOLS_PREAMBLE.
_NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use note_progress, read_file, read_many, bash, grep, glob, write, edit_file, multi_edit, apply_patch, agent_planner, agent_explorer, agent_worker, ask_user, memory_writer, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain markdown note text using the #### sections below. No XML, DSML, code fences, or tool markup.

"""

_NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain markdown note "
    "text only — the #### sections below. Tool calls will be rejected and "
    "you will fail the task."
)

_SESSION_NOTE_BODY = """You are a forked progress note-writer for this session.

Write a structure note covering the work since the last progress note (see the
<progress> block and any earlier notes above — do not repeat them).

Output markdown only — no preamble, no code fences. Use these #### sections and
omit any section with nothing new in this batch:

#### Delegation
- Subagent and key tool outcomes: kind (planner | worker | explorer | direct),
  what was dispatched, result.

#### Plan progress
- task_type, todos completed / still unchecked, user decisions.

#### Key discoveries
- Facts learned, with path:line pointers when known.

#### Blockers
- Hard facts blocking progress — never drop or weaken these.

#### Next
- Suggested follow-ups (not mandatory).

Be concrete and past-tense. Keep path:line pointers and exact verify commands."""

_TASK_NOTE_BODY = """You are a forked progress note-writer for this task.

Write a structure note covering the work since the last progress note (see the
<progress> block and any earlier notes above — do not repeat them).

Output markdown only — no preamble, no code fences. Use these #### sections and
omit any section with nothing new in this batch:

#### Work done
- Steps completed, files created/edited, commands run, with outcomes.

#### Key discoveries
- Facts learned, with path:line pointers when known.

#### Blockers / dead ends
- Hard facts blocking progress and approaches ruled out — never drop these.

#### Next
- What remains for this task.

Be concrete and past-tense. Keep path:line pointers and exact verify commands."""

NOTE_FORK_INSTRUCTION = _NO_TOOLS_PREAMBLE + _SESSION_NOTE_BODY + _NO_TOOLS_TRAILER
TASK_NOTE_FORK_INSTRUCTION = _NO_TOOLS_PREAMBLE + _TASK_NOTE_BODY + _NO_TOOLS_TRAILER
