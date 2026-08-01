"""Score an interactive episode vs original session baselines."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_BENCH = Path(__file__).resolve().parents[1]
_PIPELINE = _BENCH / "data-pipeline"
_EVAL = _BENCH.parent / "eval"
for _p in (_PIPELINE, _EVAL):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib.llm import chat_json_ex
from intent.prompts import INTENT_COVERAGE_SYSTEM

# Rubric-style judging — Claude by default (see interactive-bench/config.json
# interactive.coverage_model). Override with LB_COVERAGE_MODEL (or LB_INTERACTIVE_MODEL).
def coverage_model() -> str:
    from _lib.bench_config import resolve_coverage_model

    return resolve_coverage_model()


def judge_intent_coverage(spec: dict, episode: dict) -> dict[str, Any] | None:
    """LLM-judge which oracle intents the agent satisfied. None = judge unavailable.

    Reference metric only — never gates pass/fail, so a judge failure simply
    falls back to the revealed proxy instead of voiding the episode.
    """
    intents = [
        {"id": str(i["id"]), "text": str(i.get("text") or "")}
        for i in (spec.get("intents") or [])
    ]
    if not intents:
        return None
    messages = [str(m) for m in (episode.get("agent_messages") or []) if m]
    payload = {
        "instruction": (spec.get("instruction") or "")[:2000],
        "intents": intents,
        "agent_final_message": (messages[-1] if messages else "")[:6000],
        "earlier_agent_messages": [m[:1500] for m in messages[:-1]][-6:],
    }
    parsed, _error = chat_json_ex(
        system=INTENT_COVERAGE_SYSTEM,
        user=json.dumps(payload, ensure_ascii=False),
        model=coverage_model(),
    )
    if not parsed:
        return None
    valid_ids = {i["id"] for i in intents}
    covered = [str(x) for x in (parsed.get("covered") or []) if str(x) in valid_ids]
    missing = [i["id"] for i in intents if i["id"] not in set(covered)]
    return {
        "covered": covered,
        "missing": missing,
        "rate": len(covered) / len(intents),
        "notes": str(parsed.get("notes") or ""),
    }


def score_episode(
    spec: dict,
    episode: dict,
    *,
    tests_passed: bool | None = None,
    judge_coverage: bool = False,
) -> dict[str, Any]:
    intents = list(spec.get("intents") or [])
    revealed = set(episode.get("revealed_intent_ids") or [])
    tests_graded = tests_passed is not None

    # A sim failure voids the episode: it says nothing about the agent.
    sim_error = episode.get("sim_error")
    valid = not sim_error

    # Coverage is a reference metric. LLM judge when asked and reachable;
    # otherwise the proxy: intents revealed to the agent during the run.
    covered = [i["id"] for i in intents if str(i["id"]) in revealed]
    missing = [i["id"] for i in intents if str(i["id"]) not in revealed]
    intent_rate = (len(covered) / len(intents)) if intents else None
    coverage_mode = "revealed_proxy"
    coverage_notes = None
    if judge_coverage and valid:
        judged = judge_intent_coverage(spec, episode)
        if judged is not None:
            covered = judged["covered"]
            missing = judged["missing"]
            intent_rate = judged["rate"]
            coverage_mode = "llm_judge"
            coverage_notes = judged["notes"]

    baseline_rt = (spec.get("baseline") or {}).get("agent_runtime_sec")
    elapsed = episode.get("elapsed_sec")
    baseline_prompts = (spec.get("baseline") or {}).get("prompt_count")

    return {
        "valid": valid,
        "sim_error": sim_error,
        "tests_passed": tests_passed,
        "tests_graded": tests_graded,
        "intent_coverage": intent_rate,
        "intent_coverage_mode": coverage_mode,
        "intent_coverage_notes": coverage_notes,
        "coverage_model": coverage_model() if coverage_mode == "llm_judge" else None,
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
        # Pass = valid episode + graded tests green. Intent coverage is
        # reference-only for now.
        # TODO: once the LLM coverage judge is validated as accurate
        # (calibrated against human-labeled episodes), promote it to a hard
        # pass criterion, e.g. pass requires intent_coverage == 1.0.
        "pass": valid and bool(tests_passed),
    }
