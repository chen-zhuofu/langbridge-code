"""Tests for simulator stop rules and episode loop."""
from __future__ import annotations

import sys
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

from harness.score import score_episode  # noqa: E402
from harness.sim import (  # noqa: E402
    EpisodeState,
    SimUnavailable,
    apply_decision,
    decide_sim,
    run_episode,
    should_stop,
)
from harness.sim import SimDecision  # noqa: E402


def _spec(**kwargs):
    base = {
        "task_id": "demo",
        "instruction": "Add rate limiting",
        "intents": [
            {
                "id": "i1",
                "text": "Add rate limiting",
                "source_turn": 0,
                "revealed_at_start": True,
            },
            {
                "id": "i2",
                "text": "Return clear error codes",
                "source_turn": 1,
                "revealed_at_start": False,
            },
        ],
        "baseline": {"agent_runtime_sec": 100, "prompt_count": 2},
        "sim": {
            "noop_message": "continue",
            "max_consecutive_noops": 4,
            "timeout_sec": 10_000,
            "session_analysis": "Stay quiet until done, then reveal next.",
            "allow_offline": True,  # no LLM in tests → stay quiet instead of raising
        },
    }
    base.update(kwargs)
    return base


def test_stop_on_four_noops():
    state = EpisodeState(consecutive_noops=4)
    assert should_stop(state, _spec()) == "max_consecutive_noops"


def test_noop_does_not_count_as_user_input():
    state = EpisodeState(revealed_intent_ids=["i1"])
    msg = apply_decision(
        state,
        SimDecision(action="no-op", message="continue"),
        _spec()["intents"],
    )
    assert msg == "continue"
    assert state.user_input_count == 0
    assert state.consecutive_noops == 1


def test_reveal_counts_and_tracks_intent():
    state = EpisodeState(revealed_intent_ids=["i1"])
    apply_decision(
        state,
        SimDecision(
            action="reveal_next",
            message="Return clear error codes",
            counts_as_user_input=True,
        ),
        _spec()["intents"],
    )
    assert state.user_input_count == 1
    assert state.interventions == 1
    assert "i2" in state.revealed_intent_ids


def test_run_episode_stub_ends_and_scores():
    calls = {"n": 0}

    def agent_turn(user_text: str):
        calls["n"] += 1
        if calls["n"] >= 3:
            return {"assistant_text": "Finished. All tests pass.", "done": True}
        return {"assistant_text": "still working", "done": False}

    episode = run_episode(_spec(), agent_turn, max_turns=10)
    assert episode["stop_reason"] == "agent_done"
    assert episode["user_input_count"] >= 1  # turn0
    scored = score_episode(_spec(), episode, tests_passed=True)
    assert "intent_coverage" in scored
    assert scored["user_input_count"] == episode["user_input_count"]


def test_decide_sim_noops_without_llm():
    state = EpisodeState(revealed_intent_ids=["i1"])
    decision = decide_sim(
        spec=_spec(),
        agent_text="I am done and ready for review.",
        state=state,
    )
    assert decision.action in {"reveal_next", "no-op"}


def test_decide_sim_raises_when_llm_unavailable_and_not_offline():
    spec = _spec()
    del spec["sim"]["allow_offline"]
    with pytest.raises(SimUnavailable):
        decide_sim(spec=spec, agent_text="working", state=EpisodeState())


def test_sim_failure_voids_episode_instead_of_failing_the_agent():
    spec = _spec()
    del spec["sim"]["allow_offline"]

    def agent_turn(_user: str):
        return {"assistant_text": "working", "done": False}

    episode = run_episode(spec, agent_turn, max_turns=10)
    assert episode["stop_reason"] == "sim_error"
    assert episode["sim_error"]
    # A void episode must never be reported as an agent pass or failure.
    scored = score_episode(spec, episode, tests_passed=True)
    assert scored["valid"] is False
    assert scored["pass"] is False
