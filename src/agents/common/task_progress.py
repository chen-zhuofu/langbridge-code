"""Per-task progress notes for subagents — Session Memory Edit model.

A task's note lives at {session}/…/progress.md (per task_name). Loaded into
``<progress>`` on attach (resume) and after context compaction. Mid-turn
``note_progress`` forks an Edit-restricted writer that updates section bodies
in place and does not rewrite the pinned block.
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
        self._stack = None
        self._messages = None
        self._tool_schemas = None
        self._rounds_since_note = 0

    @property
    def enabled(self) -> bool:
        return bool(self.task_name and self.run_log_path)

    def attach(self, stack, messages, tool_schemas=None) -> None:
        """Start one dispatch: pin existing note (resume) into ``<progress>``."""
        if not self.enabled:
            return
        self._stack = stack
        self._messages = messages
        self._tool_schemas = list(tool_schemas) if tool_schemas is not None else None
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
        """Load progress.md into head ``<progress>`` (resume / compaction only)."""
        if self._stack is None or not self.enabled:
            return
        from langbridge_code.util.progress import (
            PROGRESS_HEADER,
            clip_progress_for_context,
            read_progress,
        )

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
        content = clip_progress_for_context(
            content,
            run_log_path=self.run_log_path,
            task_name=self.task_name,
        )
        self._stack.set_progress_block(content or None)
        self._sync_messages()

    def _sync_messages(self) -> None:
        if self._stack is None or self._messages is None:
            return
        rebuilt = self._stack.to_messages()
        self._messages.clear()
        self._messages.extend(rebuilt)

    def write_note(self, **_ignored) -> str:
        """Fork an Edit-restricted note-writer for this task's progress file."""
        if not self.enabled:
            return "No task progress file for this session; note not recorded."
        from langbridge_code.agents.common.fork import fork_progress_note

        try:
            result = fork_progress_note(
                self.api_key,
                self.model,
                list(self._messages or []),
                run_log_path=self.run_log_path,
                task_name=self.task_name,
                tool_schemas=self._tool_schemas,
                label=f"{self.label} note fork",
            )
        except Exception as error:
            return f"Progress note fork failed: {error}"
        # Reset only on success so a failed fork retries next round.
        if str(result).startswith("Noted"):
            self._rounds_since_note = 0
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
