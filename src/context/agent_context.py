"""Shared context-stack wiring for all agent sessions."""
from __future__ import annotations

from langbridge_code.context.common.stack import ContextStack
from langbridge_code.llm.parse import extract_output_text
from langbridge_code.util.agent_worklog import new_worklog_id
from langbridge_code.util.agent_traces import (
    append_agent_raw_round,
    reserve_agent_trace,
)


def normalize_step_items(step_items: list[dict]) -> list[dict]:
    """Store final text replies as role=assistant for downstream chat slicing."""
    normalized: list[dict] = []
    for item in step_items:
        if item.get("type") == "message":
            text = extract_output_text([item]).strip()
            if text:
                normalized.append({"role": "assistant", "content": text})
            continue
        normalized.append(item)
    return normalized


class AgentContextManager:
    """Mutates a bound message list in place so callers keep a stable reference."""

    def __init__(
        self,
        *,
        system_content: str,
        run_log_path,
        label: str,
        worklog_id=None,
        task_name: str = "",
    ):
        self.label = label
        self.run_log_path = run_log_path
        self.task_name = (task_name or "").strip()
        self.worklog_id = worklog_id
        self.last_completed_round: list[dict] = []
        self._trace_round_index = 0
        self.agent_trace_path = None
        self.agent_trace_instance_id = None
        if label != "LangBridge" and self.task_name:
            self.agent_trace_path, self.agent_trace_instance_id = reserve_agent_trace(
                run_log_path, label, self.task_name
            )
        self._stack = ContextStack(
            system_content=system_content,
            label=label,
        )
        self._messages: list[dict] | None = None

    @property
    def stack(self) -> ContextStack:
        return self._stack

    def set_worklog_id(self, run_log_path, worklog_id) -> None:
        self.run_log_path = run_log_path
        self.worklog_id = worklog_id

    def append_subagent_round(self) -> None:
        if self.label == "LangBridge" or not self.last_completed_round:
            return
        append_agent_raw_round(
            self.agent_trace_path,
            round_index=self._trace_round_index,
            messages=self.last_completed_round,
        )
        self._trace_round_index += 1

    def attach(self, messages: list[dict], *, bootstrap: bool = False) -> list[dict]:
        self._messages = messages
        if bootstrap and messages:
            self._stack.bootstrap_from_messages(messages)
        self.sync()
        return messages

    def sync(self) -> list[dict]:
        if self._messages is None:
            return self._stack.to_messages()
        rebuilt = self._stack.to_messages()
        self._messages.clear()
        self._messages.extend(rebuilt)
        return self._messages

    def begin_turn(self, user_prompt: str) -> None:
        self._stack.start_turn(user_prompt)
        self.sync()

    def after_tool_step(
        self,
        step_items: list[dict],
        *,
        model,
        budget_tokens,
    ) -> dict:
        self.last_completed_round = self._stack.complete_step(normalize_step_items(step_items))
        stats = self._stack.maybe_advance(
            model=model,
            budget_tokens=budget_tokens,
        )
        self.sync()
        return stats

    def compact_to_budget(self, *, model, budget_tokens=None) -> dict:
        """Force token-driven compaction before a model call; rebuilds messages."""
        stats = self._stack.maybe_advance(
            model=model,
            budget_tokens=budget_tokens,
        )
        if stats.get("compacted"):
            self.sync()
        return stats


def init_agent_context(
    *,
    system_prompt: str,
    run_log_path,
    label: str,
    seed_messages=None,
    task_name: str = "",
) -> tuple[list[dict], AgentContextManager, int | None]:
    if run_log_path is not None:
        from langbridge_code.agents.common.workspace import add_readable_root
        from langbridge_code.util.artifacts import artifact_dir

        # Read tools may follow absolute paths into the session artifact dir
        # (persisted explorer reports and the like) — reads only.
        add_readable_root(artifact_dir(run_log_path))
    messages = seed_messages if seed_messages is not None else [{"role": "system", "content": system_prompt}]
    context = AgentContextManager(
        system_content=system_prompt,
        run_log_path=run_log_path,
        label=label,
        task_name=task_name,
    )
    bootstrap = bool(seed_messages)
    context.attach(messages, bootstrap=bootstrap)
    worklog_id = new_worklog_id(run_log_path, label)
    if worklog_id is not None:
        context.set_worklog_id(run_log_path, worklog_id)
    return messages, context, worklog_id


def finish_step(context: AgentContextManager, step_items: list[dict], session, budget: int) -> None:
    context.after_tool_step(
        step_items,
        model=session.model,
        budget_tokens=budget,
    )
    run_log_path = getattr(session, "run_log_path", None)
    completed_round = context.last_completed_round
    if not run_log_path or not completed_round:
        return
    label = getattr(session, "label", "")
    turn_id = getattr(session, "turn_id", 0) or 0
    if label == "LangBridge":
        from langbridge_code.util.session_traces import append_raw_round

        append_raw_round(run_log_path, turn_id, completed_round)
    else:
        context.append_subagent_round()
