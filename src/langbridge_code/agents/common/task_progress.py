"""Per-task progress notes for subagents — same machinery as the main agent.

A task's notes live at {session}/progress-{task-slug}.md, derived from the
explicit task_name the main agent passes when dispatching. The file is pinned
into the subagent's context as the <progress> block, re-read after every
compaction, appended to via the same forked note-writer the main agent uses,
and compacted with the same middle-turn merge strategy. Re-dispatching the
same task_name resumes from the earlier agent's notes: each dispatch is one
"## Turn N" section in the file.
"""
from __future__ import annotations

from langbridge_code.settings import PROGRESS_NOTE_REMINDER_ROUNDS

class TaskProgress:
    """Binds one subagent session to its task progress file."""

    def __init__(
        self,
        api_key,
        model,
        run_log_path,
        task_name,
        *,
        label="Subagent",
        current_trace=None,
    ):
        self.api_key = api_key
        self.model = model
        self.run_log_path = run_log_path
        self.task_name = (task_name or "").strip()
        self.label = label
        self.current_trace = current_trace
        self.turn_id = 0
        self._stack = None
        self._messages = None
        self._tool_schemas = None
        self._rounds_since_note = 0

    @property
    def enabled(self) -> bool:
        return bool(self.task_name and self.run_log_path)

    def attach(self, stack, messages, tool_schemas=None) -> None:
        """Start one dispatch: pin existing notes and open the next turn section."""
        if not self.enabled:
            return
        from langbridge_code.util.progress import last_progress_turn_id

        self._stack = stack
        self._messages = messages
        self._tool_schemas = list(tool_schemas) if tool_schemas is not None else None
        self.turn_id = last_progress_turn_id(self.run_log_path, self.task_name) + 1
        self.refresh_block(include_traces=True)
        previous = stack.on_compacted

        def on_compacted(compacted_stack):
            if previous is not None:
                try:
                    previous(compacted_stack)
                except Exception:
                    pass
            self.refresh_block()

        stack.on_compacted = on_compacted

    def refresh_block(self, *, include_traces=False) -> None:
        if self._stack is None or not self.enabled:
            return
        from langbridge_code.util.progress import PROGRESS_HEADER, read_progress

        content = read_progress(self.run_log_path, self.task_name).strip()
        if content == PROGRESS_HEADER.strip():
            content = ""
        if include_traces:
            from langbridge_code.util.agent_traces import build_agent_resume_background

            content = build_agent_resume_background(
                self.run_log_path,
                role=self.label,
                task_name=self.task_name,
                model=self.model,
                progress=content,
                exclude_trace=self.current_trace,
            )
        self._stack.set_progress_block(content)

    def write_note(self, **_ignored) -> str:
        """note_progress tool implementation: fork a note-writer on the live context."""
        if not self.enabled:
            return "No task progress file for this session; note not recorded."
        from langbridge_code.agents.common.fork import fork_one_pass
        from langbridge_code.tools.note_progress import TASK_NOTE_FORK_INSTRUCTION
        from langbridge_code.util.progress import append_progress_note, maybe_compact_progress

        self._rounds_since_note = 0
        try:
            note = fork_one_pass(
                self.api_key,
                self.model,
                list(self._messages or []),
                TASK_NOTE_FORK_INSTRUCTION,
                label=f"{self.label} note fork",
                tool_schemas=self._tool_schemas,
            )
        except Exception as error:
            return f"Progress note fork failed: {error}"
        if not note.strip():
            return "Progress note fork returned nothing; no note recorded."
        result = append_progress_note(self.run_log_path, self.turn_id, note, self.task_name)
        maybe_compact_progress(self.api_key, self.model, self.run_log_path, self.task_name)
        return result

    def maybe_force_write(self, _context=None) -> None:
        """After too many silent rounds, fork-write this task's progress (code-enforced)."""
        if not self.enabled:
            return
        self._rounds_since_note += 1
        if self._rounds_since_note <= PROGRESS_NOTE_REMINDER_ROUNDS:
            return
        self.write_note()

    # Back-compat alias for older call sites / tests.
    maybe_remind = maybe_force_write
