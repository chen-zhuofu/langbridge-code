"""LLM intent extraction used by the curate stage.

Library only — ``analyze_one`` is called from ``curate/curate.py`` after
reference (F2P-valid tasks). Sim / coverage prompts stay in ``intent/prompts.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib.conversations import (  # noqa: E402
    first_user_prompt,
    load_conversation_turns,
    user_prompts_from_turns,
)
from _lib.llm import chat_json_ex  # noqa: E402
from _lib.quality import (  # noqa: E402
    filter_intents,
    is_noise_user_message,
    normalize_user_prompt,
)
from intent.prompts import INTENT_SYSTEM  # noqa: E402


# Careful noisy-transcript distillation — Claude by default (see
# interactive-bench/config.json pipeline.intent_model).
# Override with LB_INTENT_MODEL (or the generic LB_INTERACTIVE_MODEL).
def intent_model() -> str:
    from _lib.bench_config import resolve_intent_model

    return resolve_intent_model()


def analyze_one(inst: dict, *, data_dir: Path | None) -> dict:
    instruction = normalize_user_prompt(inst.get("instruction") or "")
    followups = [normalize_user_prompt(p) for p in (inst.get("followup_prompts") or [])]
    followups = [p for p in followups if p and not is_noise_user_message(p)]
    if data_dir is not None and (not instruction or not followups):
        turns = load_conversation_turns(data_dir, inst["session_id"])
        prompts = user_prompts_from_turns(turns)
        if prompts:
            instruction = instruction or normalize_user_prompt(prompts[0])
            if not followups:
                followups = [
                    normalize_user_prompt(p)
                    for p in prompts[1:]
                    if normalize_user_prompt(p) and not is_noise_user_message(p)
                ]
    if instruction and is_noise_user_message(instruction):
        instruction = ""

    user_blob = json.dumps(
        {
            "repo": inst.get("repo"),
            "instruction": instruction,
            "followup_prompts": followups,
            "files_touched": inst.get("files_touched") or [],
            "test_files": inst.get("test_files") or [],
            "code_files": inst.get("code_files") or [],
        },
        ensure_ascii=False,
        indent=2,
    )
    model = intent_model()
    parsed, error = chat_json_ex(system=INTENT_SYSTEM, user=user_blob, model=model)
    if not parsed:
        reason = error or "llm intent extraction failed"
        return {**inst, "_drop": True, "reason": reason}

    if not isinstance(parsed.get("intents"), list) or not parsed["intents"]:
        return {**inst, "_drop": True, "reason": "llm returned no intents"}

    raw = []
    for index, item in enumerate(parsed["intents"]):
        if not isinstance(item, dict):
            continue
        text = normalize_user_prompt(str(item.get("text") or ""))
        if not text:
            continue
        raw.append(
            {
                "id": str(item.get("id") or f"i{index + 1}"),
                "text": text,
                "source_turn": int(item.get("source_turn") or index),
                "revealed_at_start": bool(item.get("revealed_at_start", index == 0)),
            }
        )
    intents = filter_intents(raw)
    if not intents:
        return {**inst, "_drop": True, "reason": "no coding intents after filters"}

    session_analysis = str(parsed.get("session_analysis") or "").strip()
    if not session_analysis:
        return {**inst, "_drop": True, "reason": "llm returned no session_analysis"}

    out = dict(inst)
    out["instruction"] = instruction or first_user_prompt([], fallback="")
    out["intents"] = intents
    out["session_analysis"] = session_analysis
    out["intent_source"] = "llm"
    out["intent_model"] = model
    if parsed.get("task_type") in {"bug_fix", "feature", "refactor", "other"}:
        out["task_type"] = parsed["task_type"]

    if not out.get("instruction") and out["intents"]:
        out["instruction"] = out["intents"][0]["text"]
    out["instruction"] = normalize_user_prompt(out.get("instruction") or "")
    if out["instruction"] and is_noise_user_message(out["instruction"]):
        out["instruction"] = out["intents"][0]["text"]
    if not out.get("instruction"):
        return {**inst, "_drop": True, "reason": "no usable instruction after filters"}
    return out
