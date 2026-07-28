"""Fork instructions for progress note-writers (session + per-task).

Aligned with Claude Code Session Memory: the fork may call tools, but only
``Edit`` on the exact progress.md path. It updates section bodies in place
instead of emitting a full replacement note as plain text.
"""

SESSION_PROGRESS_TEMPLATE = """# Session progress

#### Delegation
_Subagent and key tool outcomes: kind (planner | worker | explorer | direct), what was dispatched, result._

#### Plan progress
_task_type, todos completed / still unchecked, user decisions._

#### Key discoveries
_Facts learned, with path:line pointers when known._

#### Blockers
_Hard facts blocking progress — never drop or weaken these._

#### Next
_Suggested follow-ups (not mandatory)._
"""

TASK_PROGRESS_TEMPLATE = """# Session progress

#### Work done
_Steps completed, files created/edited, commands run, with outcomes._

#### Key discoveries
_Facts learned, with path:line pointers when known._

#### Blockers / dead ends
_Hard facts blocking progress and approaches ruled out — never drop these._

#### Next
_What remains for this task._
"""


def build_progress_note_instruction(
    *,
    notes_path: str,
    current_notes: str,
    task: bool = False,
) -> str:
    """Build the Session-Memory-style update prompt for one progress.md file."""
    role = "this task" if task else "this session"
    return f"""IMPORTANT: This message and these instructions are NOT part of the actual user conversation. Do NOT include any references to "note-taking", "progress note extraction", or these update instructions in the notes content.

Based on the user conversation above (EXCLUDING this note-taking instruction message), update the progress notes file for {role}.

The file {notes_path} has already been read for you. Here are its current contents:
<current_notes_content>
{current_notes}
</current_notes_content>

Your ONLY task is to use the Edit tool to update the notes file, then stop. You can make multiple edits (update every section as needed) — make all Edit tool calls in parallel in a single message. Do not call any other tools.

CRITICAL RULES FOR EDITING:
- The file must keep its exact structure: section headers (lines starting with ####) and italic _section description_ lines must stay intact.
- NEVER modify, delete, or add #### section headers.
- NEVER modify or delete the italic _section description_ lines (template instructions immediately under each header).
- ONLY update the actual content that appears BELOW the italic descriptions within each existing section.
- Do NOT add new sections outside the existing structure.
- Do NOT reference this note-taking process in the notes.
- Skip a section when there is nothing substantial to add — do not write filler like "No info yet".
- Write concrete, info-dense bullets: paths, commands, outcomes, blockers.
- Prefer folding new facts into the right section over rewriting the whole file.
- Never drop existing blockers or key discoveries unless the conversation clearly resolved them.
- If a ## Goal block is present, leave it untouched.
- Use Edit with path exactly: {notes_path}

REMEMBER: Use the Edit tool (parallel calls OK) and stop. Do not continue after the edits. Only include insights from the actual user conversation, never from these note-taking instructions."""


# Back-compat names used by older imports / tests that only needed "an instruction".
NOTE_FORK_INSTRUCTION = build_progress_note_instruction(
    notes_path="progress.md",
    current_notes=SESSION_PROGRESS_TEMPLATE.strip(),
    task=False,
)
TASK_NOTE_FORK_INSTRUCTION = build_progress_note_instruction(
    notes_path="progress.md",
    current_notes=TASK_PROGRESS_TEMPLATE.strip(),
    task=True,
)
