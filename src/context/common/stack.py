"""Layered context: pinned blocks + raw rounds.

Assembled message order:
  system
  <memory>…</memory>            (main agent: prefetched user/project memories)
  [ASSIGNED_TASK] pinned user   (subagents)
  <session_memory>…</session_memory>  (main agent: session_memory.md)
  <history_conversation>…</history_conversation>  (main: prior clean Q&A)
  <skill_index>…</skill_index>  (full skill listing — cleared after compaction)
  <invoked_skills>…</invoked_skills>  (invoked skill bodies re-pinned after compaction)
  …raw rounds (tail)…

``<subagent_state>`` is not a head pin: on each change it is appended as its
own user-only raw round so the prompt prefix stays cache-stable.

Hyperparameters (settings):
  COMPACT_RAW_KEEP — raw rounds kept verbatim in the tail (default 11 — one
                     more than the session-memory cadence, so dropped rounds
                     are always covered by session_memory.md)
  COMPACT_THRESHOLD_TOKENS — when assembled context reaches this many tokens
                     (fixed, model-independent), older rounds are dropped

Flow after each agent step:
  1. Append one raw round (user message on first step of a send(), then assistant+tools).
  2. When tokens >= COMPACT_THRESHOLD_TOKENS (or the caller's tighter budget):
     drop every round except the last COMPACT_RAW_KEEP. History lives in
     session_memory.md and on-disk agent traces — no LLM prose summary. Then
     ``on_compacted`` fires so the owner can refresh <memory> / <session_memory>
     and re-pin invoked skill bodies (skill listing is dropped).
  3. Rebuild flat messages[] for the next model call.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from langbridge_code.context.common.budget import estimate_tokens
from langbridge_code.context.message import iter_tool_rounds
from langbridge_code.settings import (
    COMPACT_RAW_KEEP,
    COMPACT_THRESHOLD_TOKENS,
)

ASSIGNED_TASK_PREFIX = "[ASSIGNED_TASK]\n"
# Legacy resume sessions may still carry a prose compact user message; skip it.
_LEGACY_COMPACT_PREFIX = "[CONTEXT_COMPACT]\n"

MEMORY_TAG = "memory"
SESSION_MEMORY_TAG = "session_memory"
# Back-compat: older resumes may still carry <progress>…</progress>.
PROGRESS_TAG = SESSION_MEMORY_TAG
LEGACY_PROGRESS_TAG = "progress"
HISTORY_CONVERSATION_TAG = "history_conversation"
SUBAGENT_STATE_TAG = "subagent_state"
SYSTEM_REMINDER_TAG = "system-reminder"
SKILL_INDEX_TAG = "skill_index"
INVOKED_SKILLS_TAG = "invoked_skills"
# Published when live state clears so the latest tail message is not a stale
# RUNNING line. Kept out of ``subagent_state_block`` (that field stays None).
_SUBAGENT_STATE_CLEAR = "No subagent activity to report."
# Elapsed timers tick every step; ignore them for change detection so we do not
# flood raw_rounds (and trigger compaction) while the same workers stay running.
_SUBAGENT_STATE_ELAPSED_RE = re.compile(r"\(elapsed [^)]*\)")
_HEAD_BLOCK_TAGS = (
    MEMORY_TAG,
    SESSION_MEMORY_TAG,
    LEGACY_PROGRESS_TAG,
    HISTORY_CONVERSATION_TAG,
    SKILL_INDEX_TAG,
    INVOKED_SKILLS_TAG,
)


def _stable_subagent_state(content: str | None) -> str | None:
    text = (content or "").strip() or None
    if text is None:
        return None
    return _SUBAGENT_STATE_ELAPSED_RE.sub("(elapsed …)", text)


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


def _text_content(content: Any) -> str:
    """Plain text from string or multimodal message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    return ""


class ContextStack:
    def __init__(
        self,
        *,
        system_content: str,
        label: str = "Worker",
        raw_keep: int | None = None,
        compact_threshold_tokens: int | None = None,
    ):
        self.system_content = system_content
        self.label = label
        self.raw_keep = COMPACT_RAW_KEEP if raw_keep is None else raw_keep
        self.compact_threshold_tokens = (
            COMPACT_THRESHOLD_TOKENS
            if compact_threshold_tokens is None
            else int(compact_threshold_tokens)
        )

        self.pinned_user_content: str | None = None
        self.memory_block: str | None = None
        self.progress_block: str | None = None
        self.history_conversation_block: str | None = None
        self.subagent_state_block: str | None = None
        self.skill_index_block: str | None = None
        self.invoked_skills_block: str | None = None
        # Oldest → newest; compaction re-pins most recent first under a budget.
        self.invoked_skills: list[dict] = []
        # After compaction the full listing is dropped (Claude Code alignment).
        self.skills_listing_cleared = False
        self.raw_rounds: list[list[dict]] = []
        self.dropped_round_count = 0
        # Called after a successful drop so the owner can refresh
        # the <memory> / <session_memory> blocks (re-prefetch, re-read file).
        self.on_compacted = None

        self._pending_user: str | list[dict] | None = None

    def start_turn(self, user_content: str | list[dict]) -> None:
        self._pending_user = copy.deepcopy(user_content)

    def _set_block(self, attr: str, content: str | None) -> None:
        text = (content or "").strip()
        setattr(self, attr, text or None)

    def set_memory_block(self, content: str | None) -> None:
        self._set_block("memory_block", content)

    def set_progress_block(self, content: str | None) -> None:
        self._set_block("progress_block", content)

    def set_session_memory_block(self, content: str | None) -> None:
        """Pin ``<session_memory>`` (alias of ``set_progress_block``)."""
        self.set_progress_block(content)

    def set_history_conversation_block(self, content: str | None) -> None:
        self._set_block("history_conversation_block", content)

    def set_subagent_state_block(self, content: str | None) -> bool:
        """Append live subagent status at the tail when it changes.

        Returns True when a new raw round was appended. Unchanged content is a
        no-op so the prompt prefix stays cache-stable across steps. Elapsed-only
        timer ticks do not count as a change.
        """
        text = (content or "").strip() or None
        if _stable_subagent_state(text) == _stable_subagent_state(self.subagent_state_block):
            return False
        previous = self.subagent_state_block
        self.subagent_state_block = text
        if text is None:
            # Only publish a clear marker when leaving a non-empty state.
            if previous is None:
                return False
            body = _SUBAGENT_STATE_CLEAR
        else:
            body = text
        self.raw_rounds.append(
            [{"role": "user", "content": wrap_block(SUBAGENT_STATE_TAG, body)}]
        )
        return True

    def append_system_reminder(self, content: str) -> bool:
        """Append an unpinned system reminder at the live conversation tail.

        Some provider APIs do not allow a new system message mid-conversation,
        so LangBridge follows the established harness convention and publishes
        dynamic state as a tagged user message.  If a real user turn is pending,
        keep it first and place the reminder immediately after it.
        """
        text = (content or "").strip()
        if not text:
            return False
        if self._pending_user is not None:
            self.raw_rounds.append(
                [{"role": "user", "content": copy.deepcopy(self._pending_user)}]
            )
            self._pending_user = None
        self.raw_rounds.append(
            [{"role": "user", "content": wrap_block(SYSTEM_REMINDER_TAG, text)}]
        )
        return True

    def set_skill_index_block(self, content: str | None) -> None:
        self._set_block("skill_index_block", content)

    def set_invoked_skills_block(self, content: str | None) -> None:
        self._set_block("invoked_skills_block", content)

    def record_invoked_skill(self, name: str, body: str) -> None:
        """Track a successfully loaded skill; re-invoke moves it to most-recent."""
        skill_name = (name or "").strip()
        text = (body or "").strip()
        if not skill_name or not text:
            return
        self.invoked_skills = [
            entry for entry in self.invoked_skills if entry.get("name") != skill_name
        ]
        self.invoked_skills.append({"name": skill_name, "body": text})

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

        legacy_subagent_state: str | None = None
        while index < len(messages):
            message = messages[index]
            if message.get("role") != "user" or message.get("type"):
                break
            content = str(message.get("content", ""))
            if content.startswith(ASSIGNED_TASK_PREFIX):
                self.pinned_user_content = content
                index += 1
                continue
            if content.startswith(_LEGACY_COMPACT_PREFIX):
                index += 1
                continue
            legacy_body = unwrap_block(SUBAGENT_STATE_TAG, content)
            if legacy_body is not None:
                # Old sessions pinned this at the head; migrate to a tail round.
                legacy_subagent_state = legacy_body.strip() or None
                index += 1
                continue
            if self._absorb_block_message(content):
                index += 1
                continue
            break

        pending_user: str | list[dict] | None = None
        # Continue after head pins so mid-transcript <subagent_state> appends
        # are kept as real user rounds (not re-skipped as head blocks).
        while index < len(messages):
            message = messages[index]
            if message.get("role") == "user" and not message.get("type"):
                raw_content = message.get("content", "")
                content = _text_content(raw_content)
                if content.startswith(ASSIGNED_TASK_PREFIX):
                    index += 1
                    continue
                if content.startswith(_LEGACY_COMPACT_PREFIX):
                    index += 1
                    continue
                if any(content.startswith(f"<{tag}>") for tag in _HEAD_BLOCK_TAGS):
                    index += 1
                    continue
                state_body = unwrap_block(SUBAGENT_STATE_TAG, content)
                if state_body is not None:
                    stripped = state_body.strip()
                    if not stripped or stripped == _SUBAGENT_STATE_CLEAR:
                        self.subagent_state_block = None
                    else:
                        self.subagent_state_block = stripped
                if pending_user is not None:
                    # Consecutive user-only updates (e.g. state appends).
                    self.raw_rounds.append(
                        [{"role": "user", "content": copy.deepcopy(pending_user)}]
                    )
                pending_user = copy.deepcopy(raw_content)
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
            if message.get("type") in {
                "reasoning",
                "function_call",
                "function_call_output",
                "tool_search_call",
                "tool_search_output",
            }:
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

        self._materialize_legacy_head_subagent_state(legacy_subagent_state)

    def _materialize_legacy_head_subagent_state(self, body: str | None) -> None:
        """Move a resumed head-pinned <subagent_state> into a tail raw round."""
        if self._latest_subagent_state_body() is not None:
            return
        text = (body or "").strip()
        if not text:
            return
        self.subagent_state_block = None
        self.set_subagent_state_block(text)

    def _latest_subagent_state_body(self) -> str | None:
        for round_messages in reversed(self.raw_rounds):
            for message in reversed(round_messages):
                if message.get("role") != "user" or message.get("type"):
                    continue
                body = unwrap_block(SUBAGENT_STATE_TAG, str(message.get("content", "")))
                if body is not None:
                    return body or None
        if self._pending_user is not None:
            body = unwrap_block(SUBAGENT_STATE_TAG, _text_content(self._pending_user))
            if body is not None:
                return body or None
        return None

    def _absorb_block_message(self, content: str) -> bool:
        for tag, attr in (
            (MEMORY_TAG, "memory_block"),
            (SESSION_MEMORY_TAG, "progress_block"),
            (LEGACY_PROGRESS_TAG, "progress_block"),
            (HISTORY_CONVERSATION_TAG, "history_conversation_block"),
            (SKILL_INDEX_TAG, "skill_index_block"),
            (INVOKED_SKILLS_TAG, "invoked_skills_block"),
        ):
            body = unwrap_block(tag, content)
            if body is not None:
                setattr(self, attr, body or None)
                if tag == INVOKED_SKILLS_TAG and body:
                    # Resume after a compacted session: listing was dropped.
                    self.skills_listing_cleared = True
                return True
        return False

    def _consume_tool_round(self, messages: list[dict], index: int) -> tuple[list[dict], int]:
        rounds = iter_tool_rounds(messages[index:])
        if not rounds:
            return [], index + 1
        _, indices = rounds[0]
        items = [copy.deepcopy(messages[index + offset]) for offset in indices]
        return items, index + max(indices) + 1

    def complete_step(self, step_items: list[dict]) -> list[dict]:
        """Record one agent step (assistant output + tool results)."""
        round_messages: list[dict] = []
        if self._pending_user is not None:
            round_messages.append(
                {"role": "user", "content": copy.deepcopy(self._pending_user)}
            )
            self._pending_user = None
        round_messages.extend(copy.deepcopy(step_items))
        self.raw_rounds.append(round_messages)
        return copy.deepcopy(round_messages)

    def maybe_advance(
        self,
        *,
        model: str | None = None,
        budget_tokens: int | None = None,
    ) -> dict:
        """Drop older rounds when over budget. Returns a stats dict."""
        stats = {
            "compacted": False,
            "tokens": self.token_count(),
        }

        if self._should_compact(model, budget_tokens):
            if self._compact():
                stats["compacted"] = True
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
        # <session_memory> only at the head after resume / compaction — mid-turn
        # update_session_memory overrides the file but does not rewrite this block.
        if self.progress_block:
            messages.append(
                {
                    "role": "user",
                    "content": wrap_block(SESSION_MEMORY_TAG, self.progress_block),
                }
            )
        if self.history_conversation_block:
            messages.append(
                {
                    "role": "user",
                    "content": wrap_block(
                        HISTORY_CONVERSATION_TAG, self.history_conversation_block
                    ),
                }
            )
        # <subagent_state> is append-on-change inside raw_rounds (cache-stable).
        if self.skill_index_block:
            messages.append(
                {"role": "user", "content": wrap_block(SKILL_INDEX_TAG, self.skill_index_block)}
            )
        if self.invoked_skills_block:
            messages.append(
                {
                    "role": "user",
                    "content": wrap_block(INVOKED_SKILLS_TAG, self.invoked_skills_block),
                }
            )
        for round_messages in self.raw_rounds:
            messages.extend(copy.deepcopy(round_messages))
        if self._pending_user is not None:
            messages.append(
                {"role": "user", "content": copy.deepcopy(self._pending_user)}
            )
        return messages

    def token_count(self) -> int:
        return estimate_tokens(self.to_messages())

    def layer_snapshot(self) -> dict:
        return {
            "pinned_user": bool(self.pinned_user_content),
            "memory_block": bool(self.memory_block),
            "progress_block": bool(self.progress_block),
            "history_conversation_block": bool(self.history_conversation_block),
            "subagent_state_block": bool(self.subagent_state_block),
            "skill_index_block": bool(self.skill_index_block),
            "invoked_skills_block": bool(self.invoked_skills_block),
            "invoked_skill_count": len(self.invoked_skills),
            "raw_round_count": len(self.raw_rounds),
            "dropped_round_count": self.dropped_round_count,
            "pending_user": self._pending_user is not None,
        }

    def _should_compact(self, model: str | None, budget_tokens: int | None) -> bool:
        del model  # threshold is fixed; model window is not used
        if len(self.raw_rounds) <= self.raw_keep:
            return False
        threshold = self.compact_threshold_tokens
        if budget_tokens is not None:
            threshold = min(threshold, budget_tokens)
        return self.token_count() >= threshold

    def _compact(self) -> bool:
        drop_count = len(self.raw_rounds) - self.raw_keep
        if drop_count <= 0:
            return False
        self.raw_rounds = self.raw_rounds[drop_count:]
        self.dropped_round_count += drop_count
        self._republish_subagent_state_after_compact()
        return True

    def _republish_subagent_state_after_compact(self) -> None:
        """Re-append current state when compaction dropped the latest update."""
        text = self.subagent_state_block
        if not text:
            return
        if self._latest_subagent_state_body() is not None:
            return
        self.subagent_state_block = None
        self.set_subagent_state_block(text)
