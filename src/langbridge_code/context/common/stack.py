"""Layered context: pinned blocks + compact prose + raw rounds.

Assembled message order:
  system
  <memory>…</memory>            (main agent: prefetched user/project memories)
  [ASSIGNED_TASK] pinned user   (subagents)
  <progress>…</progress>        (main agent: progress.md)
  <skill_index>…</skill_index>  (task-relevant skill index lines)
  [CONTEXT_COMPACT] prose       (compressed middle, after compaction)
  …raw rounds (tail)…

Hyperparameters (settings):
  COMPACT_RAW_KEEP — raw rounds kept verbatim in the tail (default 11 — one
                     more than the progress-note cadence, so the compressed
                     middle always overlaps progress.md)
  COMPACT_FRACTION — when assembled context reaches this fraction of the
                     model window, older rounds are compressed into prose

Flow after each agent step:
  1. Append one raw round (user message on first step of a send(), then assistant+tools).
  2. When tokens >= window * FRACTION (or the caller's budget): merge every
     round except the last COMPACT_RAW_KEEP, plus any prior prose, into one
     compact prose block via one LLM call. Then ``on_compacted`` fires so the
     owner can drop the stale <memory> head, re-prefetch it, and re-read
     progress.md.
  3. Rebuild flat messages[] for the next model call.
"""
from __future__ import annotations

import copy

from langbridge_code.context.common.budget import estimate_tokens
from langbridge_code.context.debug import format_raws, record_prose_compression
from langbridge_code.context.message import iter_tool_rounds
from langbridge_code.context.prose import COMPACT_PROSE_PREFIX, compact_rounds_to_prose
from langbridge_code.llm.model_context import model_context_window
from langbridge_code.settings import (
    COMPACT_FRACTION,
    COMPACT_RAW_KEEP,
    COMPACT_USE_LLM,
)

ASSIGNED_TASK_PREFIX = "[ASSIGNED_TASK]\n"

MEMORY_TAG = "memory"
PROGRESS_TAG = "progress"
SKILL_INDEX_TAG = "skill_index"


def wrap_block(tag: str, content: str) -> str:
    return f"<{tag}>\n{content.strip()}\n</{tag}>"


def unwrap_block(tag: str, content: str) -> str | None:
    opening = f"<{tag}>"
    if not content.startswith(opening):
        return None
    body = content[len(opening) :]
    closing = f"</{tag}>"
    if body.rstrip().endswith(closing):
        body = body.rstrip()[: -len(closing)]
    return body.strip()


class ContextStack:
    def __init__(
        self,
        *,
        system_content: str,
        label: str = "Worker",
        raw_keep: int | None = None,
        compact_fraction: float | None = None,
        prose_compactor=None,
    ):
        self.system_content = system_content
        self.label = label
        self.raw_keep = COMPACT_RAW_KEEP if raw_keep is None else raw_keep
        self.compact_fraction = (
            COMPACT_FRACTION if compact_fraction is None else compact_fraction
        )
        self._prose_compactor = prose_compactor or compact_rounds_to_prose

        self.compact_prose: str | None = None
        self.pinned_user_content: str | None = None
        self.memory_block: str | None = None
        self.progress_block: str | None = None
        self.skill_index_block: str | None = None
        self.raw_rounds: list[list[dict]] = []
        # Called after a successful prose compaction so the owner can refresh
        # the <memory> / <progress> blocks (re-prefetch, re-read progress.md).
        self.on_compacted = None

        self._pending_user: str | None = None

    def start_turn(self, user_content: str) -> None:
        self._pending_user = user_content

    def _set_block(self, attr: str, content: str | None) -> None:
        text = (content or "").strip()
        setattr(self, attr, text or None)

    def set_memory_block(self, content: str | None) -> None:
        self._set_block("memory_block", content)

    def set_progress_block(self, content: str | None) -> None:
        self._set_block("progress_block", content)

    def set_skill_index_block(self, content: str | None) -> None:
        self._set_block("skill_index_block", content)

    def set_pinned_user(self, content: str | None) -> None:
        """Fixed user message prepended on every to_messages(); never compacted."""
        if content and str(content).strip():
            self.pinned_user_content = str(content).strip()
        else:
            self.pinned_user_content = None

    def set_pinned_assigned_task(self, task: str) -> None:
        text = (task or "").strip()
        self.set_pinned_user(f"{ASSIGNED_TASK_PREFIX}{text}" if text else None)

    def bootstrap_from_messages(self, messages: list[dict]) -> None:
        """Import a flat message list (session resume) into layered state."""
        if not messages:
            return
        index = 0
        if messages[0].get("role") == "system":
            self.system_content = str(messages[0].get("content", ""))
            index = 1

        while index < len(messages):
            message = messages[index]
            if message.get("role") != "user" or message.get("type"):
                break
            content = str(message.get("content", ""))
            if content.startswith(ASSIGNED_TASK_PREFIX):
                self.pinned_user_content = content
                index += 1
                continue
            if content.startswith(COMPACT_PROSE_PREFIX):
                self.compact_prose = content[len(COMPACT_PROSE_PREFIX) :]
                index += 1
                continue
            if self._absorb_block_message(content):
                index += 1
                continue
            break

        pending_user: str | None = None
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.get("role") == "user" and not message.get("type"):
                content = str(message.get("content", ""))
                if content.startswith(ASSIGNED_TASK_PREFIX):
                    index += 1
                    continue
                if content.startswith(COMPACT_PROSE_PREFIX):
                    index += 1
                    continue
                if any(
                    content.startswith(f"<{tag}>")
                    for tag in (MEMORY_TAG, PROGRESS_TAG, SKILL_INDEX_TAG)
                ):
                    index += 1
                    continue
                pending_user = content
                index += 1
                continue
            if message.get("role") == "assistant":
                round_items: list[dict] = []
                if pending_user is not None:
                    round_items.append({"role": "user", "content": pending_user})
                    pending_user = None
                round_items.append(copy.deepcopy(message))
                index += 1
                self.raw_rounds.append(round_items)
                continue
            if message.get("type") in {"reasoning", "function_call", "function_call_output"}:
                round_items = []
                if pending_user is not None:
                    round_items.append({"role": "user", "content": pending_user})
                    pending_user = None
                tool_items, index = self._consume_tool_round(messages, index)
                round_items.extend(tool_items)
                if round_items:
                    self.raw_rounds.append(round_items)
                continue
            index += 1

        if pending_user is not None:
            self._pending_user = pending_user

    def _absorb_block_message(self, content: str) -> bool:
        for tag, attr in (
            (MEMORY_TAG, "memory_block"),
            (PROGRESS_TAG, "progress_block"),
            (SKILL_INDEX_TAG, "skill_index_block"),
        ):
            body = unwrap_block(tag, content)
            if body is not None:
                setattr(self, attr, body or None)
                return True
        return False

    def _consume_tool_round(self, messages: list[dict], index: int) -> tuple[list[dict], int]:
        rounds = iter_tool_rounds(messages[index:])
        if not rounds:
            return [], index + 1
        _, indices = rounds[0]
        items = [copy.deepcopy(messages[index + offset]) for offset in indices]
        return items, index + max(indices) + 1

    def complete_step(self, step_items: list[dict]) -> None:
        """Record one agent step (assistant output + tool results)."""
        round_messages: list[dict] = []
        if self._pending_user is not None:
            round_messages.append({"role": "user", "content": self._pending_user})
            self._pending_user = None
        round_messages.extend(copy.deepcopy(step_items))
        self.raw_rounds.append(round_messages)

    def maybe_advance(
        self,
        *,
        api_key: str | None,
        model: str | None,
        budget_tokens: int | None = None,
    ) -> dict:
        """Compress older rounds into prose when over budget. Returns a stats dict."""
        stats = {
            "prose_compacted": False,
            "tokens": self.token_count(),
        }
        if not (api_key and model and COMPACT_USE_LLM):
            return stats

        if self._should_compact(model, budget_tokens):
            if self._compact(api_key, model):
                stats["prose_compacted"] = True
                if self.on_compacted is not None:
                    try:
                        self.on_compacted(self)
                    except Exception:
                        pass

        stats["tokens"] = self.token_count()
        return stats

    def to_messages(self) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": self.system_content}]
        if self.memory_block:
            messages.append({"role": "user", "content": wrap_block(MEMORY_TAG, self.memory_block)})
        if self.pinned_user_content:
            messages.append({"role": "user", "content": self.pinned_user_content})
        if self.progress_block:
            messages.append({"role": "user", "content": wrap_block(PROGRESS_TAG, self.progress_block)})
        if self.skill_index_block:
            messages.append(
                {"role": "user", "content": wrap_block(SKILL_INDEX_TAG, self.skill_index_block)}
            )
        if self.compact_prose:
            messages.append(
                {
                    "role": "user",
                    "content": COMPACT_PROSE_PREFIX + self.compact_prose,
                }
            )
        for round_messages in self.raw_rounds:
            messages.extend(copy.deepcopy(round_messages))
        if self._pending_user is not None:
            messages.append({"role": "user", "content": self._pending_user})
        return messages

    def token_count(self) -> int:
        return estimate_tokens(self.to_messages())

    def layer_snapshot(self) -> dict:
        return {
            "compact_prose": bool(self.compact_prose),
            "pinned_user": bool(self.pinned_user_content),
            "memory_block": bool(self.memory_block),
            "progress_block": bool(self.progress_block),
            "skill_index_block": bool(self.skill_index_block),
            "raw_round_count": len(self.raw_rounds),
            "pending_user": self._pending_user is not None,
        }

    def _should_compact(self, model: str | None, budget_tokens: int | None) -> bool:
        if len(self.raw_rounds) <= self.raw_keep:
            return False
        window = model_context_window(model or "")
        threshold = int(window * self.compact_fraction)
        if budget_tokens is not None:
            threshold = min(threshold, budget_tokens)
        return self.token_count() >= threshold

    def _compact(self, api_key: str, model: str) -> bool:
        batch = self.raw_rounds[: len(self.raw_rounds) - self.raw_keep]
        if not batch:
            return False
        prior_prose = self.compact_prose
        merged = self._prose_compactor(
            api_key,
            model,
            compact_prose=prior_prose,
            rounds=batch,
            label=f"{self.label} prose compact",
        )
        if not merged.strip():
            return False
        input_parts = []
        if prior_prose:
            input_parts.append(f"## Prior prose\n\n{prior_prose}")
        input_parts.append(f"## Raw rounds\n\n{format_raws(batch)}")
        record_prose_compression(
            input_text="\n\n".join(input_parts),
            output=merged,
        )
        self.compact_prose = merged
        self.raw_rounds = self.raw_rounds[len(batch) :]
        return True
