"""Shared context-stack wiring for all agent sessions."""
from __future__ import annotations

from langbridge_code.context.common.stack import ContextStack
from langbridge_code.llm.parse import extract_output_text
from langbridge_code.util.agent_worklog import new_worklog_id


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
    ):
        self.label = label
        self._stack = ContextStack(
            system_content=system_content,
            label=label,
        )
        self._messages: list[dict] | None = None

    @property
    def stack(self) -> ContextStack:
        return self._stack

    def set_worklog_id(self, run_log_path, worklog_id) -> None:
        del run_log_path, worklog_id

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
        api_key,
        model,
        budget_tokens,
    ) -> dict:
        self._stack.complete_step(normalize_step_items(step_items))
        stats = self._stack.maybe_advance(
            api_key=api_key,
            model=model,
            budget_tokens=budget_tokens,
        )
        self.sync()
        return stats

    def compact_to_budget(self, *, api_key, model, budget_tokens=None) -> dict:
        """Force token-driven compaction before a model call; rebuilds messages."""
        stats = self._stack.maybe_advance(
            api_key=api_key,
            model=model,
            budget_tokens=budget_tokens,
        )
        if stats.get("prose_compacted"):
            self.sync()
        return stats


def init_agent_context(
    *,
    system_prompt: str,
    run_log_path,
    label: str,
    seed_messages=None,
) -> tuple[list[dict], AgentContextManager, int | None]:
    messages = seed_messages if seed_messages is not None else [{"role": "system", "content": system_prompt}]
    context = AgentContextManager(
        system_content=system_prompt,
        run_log_path=run_log_path,
        label=label,
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
        api_key=session.api_key,
        model=session.model,
        budget_tokens=budget,
    )
    run_log_path = getattr(session, "run_log_path", None)
    rounds = context.stack.raw_rounds
    if not run_log_path or not rounds:
        return
    label = getattr(session, "label", "")
    turn_id = getattr(session, "turn_id", 0) or 0
    from langbridge_code.util.session_traces import append_agent_trace_round, append_raw_round

    append_agent_trace_round(
        run_log_path,
        label,
        getattr(session, "worklog_id", None),
        turn_id,
        rounds[-1],
        step=getattr(session, "step", None),
    )
    if label == "LangBridge":
        append_raw_round(run_log_path, turn_id, rounds[-1])
