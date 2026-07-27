"""Score an interactive episode vs original session baselines."""
from __future__ import annotations

from typing import Any


def score_episode(spec: dict, episode: dict, *, tests_passed: bool | None = None) -> dict[str, Any]:
    intents = list(spec.get("intents") or [])
    revealed = set(episode.get("revealed_intent_ids") or [])
    # Placeholder coverage: intents revealed during the run (LLM judge optional later).
    covered = [i["id"] for i in intents if str(i["id"]) in revealed]
    missing = [i["id"] for i in intents if str(i["id"]) not in revealed]
    intent_rate = (len(covered) / len(intents)) if intents else None
    tests_graded = tests_passed is not None

    baseline_rt = (spec.get("baseline") or {}).get("agent_runtime_sec")
    elapsed = episode.get("elapsed_sec")
    baseline_prompts = (spec.get("baseline") or {}).get("prompt_count")

    return {
        "tests_passed": tests_passed,
        "tests_graded": tests_graded,
        "intent_coverage": intent_rate,
        "intent_coverage_mode": "revealed_proxy",
        "intents_covered": covered,
        "intents_missing": missing,
        "user_input_count": episode.get("user_input_count"),
        "interventions": episode.get("interventions"),
        "elapsed_sec": elapsed,
        "baseline_agent_runtime_sec": baseline_rt,
        "runtime_ratio": (
            None
            if baseline_rt in (None, 0) or elapsed is None
            else float(elapsed) / float(baseline_rt)
        ),
        "baseline_prompt_count": baseline_prompts,
        "input_ratio": (
            None
            if not baseline_prompts or episode.get("user_input_count") is None
            else float(episode["user_input_count"]) / float(baseline_prompts)
        ),
        "stop_reason": episode.get("stop_reason"),
        # Real pass requires graded tests. Stub runs leave tests_passed=None → pass=false.
        "pass": bool(tests_passed) and (intent_rate == 1.0 if intent_rate is not None else False),
    }
