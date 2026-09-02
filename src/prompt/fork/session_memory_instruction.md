<note-taking-instructions>
IMPORTANT: This message and these instructions are NOT part of the actual user conversation. Do NOT include any references to "note-taking", "session memory extraction", or these update instructions in the notes content.

Based on the user conversation above (EXCLUDING this note-taking instruction message), update the session memory file for {role}.

The file {notes_path} has already been read for you. Here are its current contents:
<current_notes_content>
{current_notes}
</current_notes_content>

Your ONLY task is to use the Edit tool to update the notes file, when finished, STOP.
Only Edit on {notes_path} is allowed. Do NOT call any other tools.
You can make multiple edits (update every section as needed) — make all Edit tool calls in parallel in a single message.

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

REMEMBER: Your ONLY task is to use the Edit tool to update the notes file, when finished, STOP.
Only Edit on {notes_path} is allowed. Do NOT call any other tools.
Only include insights from the actual user conversation. Do NOT include these note-taking instructions.
</note-taking-instructions>
