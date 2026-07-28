"""User-simulator policy and stop rules for interactive eval."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import sys

_BENCH = Path(__file__).resolve().parents[1]
_PIPELINE = _BENCH / "data-pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from intent.prompts import SIM_SYSTEM
from _lib.llm import chat_json
from _lib.runtime import eval_timeout_sec

NOOP_MESSAGE = "continue"
MAX_CONSECUTIVE_NOOPS = 4
SPEAKING_ACTIONS = frozenset({"steer", "reveal_next", "answer"})


@dataclass
class SimDecision:
    action: str
    message: str
    reason: str = ""
    counts_as_user_input: bool = False


@dataclass
class EpisodeState:
    consecutive_noops: int = 0
    user_input_count: int = 0  # excludes "continue"
    interventions: int = 0  # steer/reveal/answer
    revealed_intent_ids: list[str] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    stop_reason: str | None = None


def initial_revealed(intents: list[dict]) -> list[str]:
    ids = []
    for item in intents:
        if item.get("revealed_at_start", False) or item.get("source_turn", 1) == 0:
            ids.append(str(item["id"]))
    if not ids and intents:
        ids.append(str(intents[0]["id"]))
    return ids


def timeout_sec(spec: dict) -> float | None:
    """Episode e2e ceiling: explicit sim.timeout_sec, else max(40m, 2×runtime)."""
    sim = spec.get("sim") or {}
    if sim.get("timeout_sec") is not None:
        return float(sim["timeout_sec"])
    baseline = (spec.get("baseline") or {}).get("agent_runtime_sec")
    return eval_timeout_sec(baseline)

def should_stop(state: EpisodeState, spec: dict) -> str | None:
    max_noops = int((spec.get("sim") or {}).get("max_consecutive_noops") or MAX_CONSECUTIVE_NOOPS)
    if state.consecutive_noops >= max_noops:
        return "max_consecutive_noops"
    limit = timeout_sec(spec)
    if limit is not None and (time.monotonic() - state.started_at) >= limit:
        return "timeout"
    return None


def heuristic_sim(
    *,
    agent_text: str,
    intents: list[dict],
    revealed: list[str],
) -> SimDecision:
    """Offline fallback when no LLM key: reveal next when agent looks done."""
    lower = (agent_text or "").lower()
    asked = "?" in (agent_text or "") and any(
        w in lower for w in ("should i", "do you want", "which", "prefer", "?")
    )
    unrevealed = [i for i in intents if str(i["id"]) not in set(revealed)]
    if asked and unrevealed:
        return SimDecision(
            action="answer",
            message=f"Please stick to: {unrevealed[0]['text']}",
            reason="agent asked a question",
            counts_as_user_input=True,
        )
    doneish = any(
        p in lower
        for p in ("done", "finished", "all tests pass", "ready for review", "completed")
    )
    if doneish and unrevealed:
        nxt = unrevealed[0]
        return SimDecision(
            action="reveal_next",
            message=str(nxt["text"]),
            reason="agent looks done; reveal next intent",
            counts_as_user_input=True,
        )
    return SimDecision(action="no-op", message=NOOP_MESSAGE, reason="progress / default")


def llm_sim(
    *,
    spec: dict,
    agent_text: str,
    state: EpisodeState,
    trajectory_summary: str = "",
) -> SimDecision | None:
    intents = list(spec.get("intents") or [])
    payload = {
        "session_analysis": (spec.get("sim") or {}).get("session_analysis")
        or spec.get("session_analysis")
        or "",
        "intents": intents,
        "revealed_intent_ids": state.revealed_intent_ids,
        "agent_message": (agent_text or "")[:4000],
        "trajectory_summary": trajectory_summary[:4000],
    }
    parsed = chat_json(system=SIM_SYSTEM, user=json.dumps(payload, ensure_ascii=False))
    if not parsed:
        return None
    action = str(parsed.get("action") or "no-op").strip()
    if action not in {"no-op", "steer", "reveal_next", "answer"}:
        action = "no-op"
    message = str(parsed.get("message") or "").strip()
    if action == "no-op":
        message = (spec.get("sim") or {}).get("noop_message") or NOOP_MESSAGE
    elif not message and action == "reveal_next":
        for item in intents:
            if str(item["id"]) not in set(state.revealed_intent_ids):
                message = str(item["text"])
                break
    return SimDecision(
        action=action,
        message=message,
        reason=str(parsed.get("reason") or ""),
        counts_as_user_input=action in SPEAKING_ACTIONS,
    )


def decide_sim(
    *,
    spec: dict,
    agent_text: str,
    state: EpisodeState,
    trajectory_summary: str = "",
) -> SimDecision:
    decision = llm_sim(
        spec=spec,
        agent_text=agent_text,
        state=state,
        trajectory_summary=trajectory_summary,
    )
    if decision is None:
        decision = heuristic_sim(
            agent_text=agent_text,
            intents=list(spec.get("intents") or []),
            revealed=state.revealed_intent_ids,
        )
    return decision


def apply_decision(state: EpisodeState, decision: SimDecision, intents: list[dict]) -> str:
    """Update counters; return the user message to inject for the next agent turn."""
    if decision.action == "no-op":
        state.consecutive_noops += 1
        msg = decision.message or NOOP_MESSAGE
    else:
        state.consecutive_noops = 0
        state.user_input_count += 1
        state.interventions += 1
        msg = decision.message
        if decision.action == "reveal_next":
            for item in intents:
                iid = str(item["id"])
                if iid not in set(state.revealed_intent_ids):
                    state.revealed_intent_ids.append(iid)
                    break
    state.turns.append(
        {
            "action": decision.action,
            "message": msg,
            "reason": decision.reason,
            "counts_as_user_input": decision.counts_as_user_input
            or decision.action in SPEAKING_ACTIONS,
        }
    )
    return msg


AgentTurnFn = Callable[[str], dict[str, Any]]
"""Inject user text → {assistant_text, done?, error?}."""


def run_episode(
    spec: dict,
    agent_turn: AgentTurnFn,
    *,
    max_turns: int = 40,
) -> dict[str, Any]:
    """Drive turn-0 instruction then sim loop until stop.

    ``agent_turn`` must run one agent response cycle given the latest user text.
    Sim / intent judge never end the episode — only noop cap / timeout / agent done.
    """
    intents = list(spec.get("intents") or [])
    state = EpisodeState(revealed_intent_ids=initial_revealed(intents))
    # Turn 0 counts as one real user input (original instruction).
    instruction = spec.get("instruction") or ""
    state.user_input_count = 1 if instruction else 0
    state.turns.append(
        {
            "action": "turn0",
            "message": instruction,
            "reason": "verbatim first user message",
            "counts_as_user_input": True,
        }
    )

    agent_messages: list[str] = []
    user_text = instruction
    for _ in range(max_turns):
        stop = should_stop(state, spec)
        if stop:
            state.stop_reason = stop
            break
        result = agent_turn(user_text)
        assistant = str(result.get("assistant_text") or "")
        agent_messages.append(assistant)
        if result.get("error"):
            state.stop_reason = f"agent_error: {result['error']}"
            break
        if result.get("done"):
            state.stop_reason = "agent_done"
            break
        decision = decide_sim(spec=spec, agent_text=assistant, state=state)
        user_text = apply_decision(state, decision, intents)
        stop = should_stop(state, spec)
        if stop:
            state.stop_reason = stop
            break

    if state.stop_reason is None:
        state.stop_reason = "max_turns"

    elapsed = time.monotonic() - state.started_at
    return {
        "task_id": spec.get("task_id"),
        "stop_reason": state.stop_reason,
        "user_input_count": state.user_input_count,
        "interventions": state.interventions,
        "consecutive_noops_final": state.consecutive_noops,
        "elapsed_sec": elapsed,
        "timeout_sec": timeout_sec(spec),
        "baseline_agent_runtime_sec": (spec.get("baseline") or {}).get("agent_runtime_sec"),
        "baseline_prompt_count": (spec.get("baseline") or {}).get("prompt_count"),
        "sim_turns": state.turns,
        "agent_messages": agent_messages,
        "revealed_intent_ids": state.revealed_intent_ids,
    }
