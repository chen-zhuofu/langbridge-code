"""Extract atomic intents + session_analysis from SWE-Chat prompts.

Primary call site is ``curate/curate.py`` (after env/reference), so LLM cost
is paid only for tasks that survive Docker + F2P. This module remains a
standalone CLI for debugging.

```bash
uv run python interactive-bench/data-pipeline/intent/analyze.py --limit 20
uv run python interactive-bench/data-pipeline/intent/analyze.py --data-dir /path/to/swe-chat --limit 5
```

Without an API key, falls back to heuristic intents (one per user prompt).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.conversations import (  # noqa: E402
    first_user_prompt,
    load_conversation_turns,
    user_prompts_from_turns,
)
from _lib.io_util import append_drop, load_json, load_jsonl, write_jsonl  # noqa: E402
from _lib.labels import task_type_from_prompt_intents  # noqa: E402
from _lib.llm import chat_json  # noqa: E402
from _lib.quality import clean_user_text, filter_intents, is_meta_intent  # noqa: E402
from intent.prompts import INTENT_SYSTEM  # noqa: E402


def heuristic_intents(instruction: str, followups: list[str]) -> list[dict]:
    raw: list[dict] = []
    instruction = clean_user_text(instruction)
    if instruction and not is_meta_intent(instruction):
        raw.append(
            {
                "id": "i1",
                "text": instruction[:500],
                "source_turn": 0,
                "revealed_at_start": True,
            }
        )
    for index, text in enumerate(followups, start=1):
        text = clean_user_text(str(text))
        if not text or is_meta_intent(text):
            continue
        raw.append(
            {
                "id": f"i{index + 1}",
                "text": text[:500],
                "source_turn": index,
                "revealed_at_start": False,
            }
        )
    return filter_intents(raw)


def heuristic_session_analysis(intents: list[dict]) -> str:
    lines = [
        "Stay silent while the agent makes progress on the current revealed intent.",
        "Steer if the agent drifts off-topic or misses a stated constraint.",
        "Reveal the next unrevealed intent only when the current one looks complete or the agent is wrapping up.",
        "Answer agent questions using only known intents; do not expand scope.",
    ]
    if len(intents) > 1:
        lines.append(
            f"There are {len(intents)} intents; only the first is revealed at start."
        )
    return "\n".join(lines)


def analyze_one(inst: dict, *, data_dir: Path | None) -> dict:
    instruction = clean_user_text(inst.get("instruction") or "")
    followups = [clean_user_text(p) for p in (inst.get("followup_prompts") or [])]
    followups = [p for p in followups if p]
    if data_dir is not None and (not instruction or not followups):
        turns = load_conversation_turns(data_dir, inst["session_id"])
        prompts = user_prompts_from_turns(turns)
        if prompts:
            instruction = instruction or prompts[0]
            if not followups:
                followups = prompts[1:]

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
    parsed = chat_json(system=INTENT_SYSTEM, user=user_blob)
    out = dict(inst)
    out["instruction"] = instruction or first_user_prompt([], fallback="")

    if parsed and isinstance(parsed.get("intents"), list) and parsed["intents"]:
        raw = []
        for index, item in enumerate(parsed["intents"]):
            if not isinstance(item, dict):
                continue
            text = clean_user_text(str(item.get("text") or ""))
            if not text:
                continue
            raw.append(
                {
                    "id": str(item.get("id") or f"i{index + 1}"),
                    "text": text,
                    "source_turn": int(item.get("source_turn") or index),
                    "revealed_at_start": bool(
                        item.get("revealed_at_start", index == 0)
                    ),
                }
            )
        intents = filter_intents(raw) or heuristic_intents(instruction, followups)
        out["intents"] = intents
        out["session_analysis"] = str(
            parsed.get("session_analysis") or heuristic_session_analysis(intents)
        )
        if parsed.get("task_type") in {"bug_fix", "feature", "refactor", "other"}:
            out["task_type"] = parsed["task_type"]
    else:
        intents = heuristic_intents(instruction, followups)
        if not intents:
            return {**inst, "_drop": True, "reason": "no user prompts for intents"}
        out["intents"] = intents
        out["session_analysis"] = heuristic_session_analysis(intents)
        out["intent_source"] = "heuristic"
        if not out.get("task_type") or out["task_type"] == "other":
            out["task_type"] = task_type_from_prompt_intents(inst.get("prompt_intents"))

    if not out.get("instruction") and out["intents"]:
        out["instruction"] = out["intents"][0]["text"]
    out["instruction"] = clean_user_text(out.get("instruction") or "")
    if not out.get("intents"):
        return {**inst, "_drop": True, "reason": "no coding intents after filters"}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--in", dest="inp", type=Path, default=paths.DEFAULT_ENRICH_JSONL)
    parser.add_argument("--out", type=Path, default=paths.DEFAULT_INTENT_JSONL)
    parser.add_argument("--drop", type=Path, default=paths.DEFAULT_INTENT_DROP)
    args = parser.parse_args()

    done = {r["task_id"] for r in load_jsonl(args.out) if r.get("task_id")}
    if args.drop.exists():
        for entry in load_json(args.drop).get("dropped") or []:
            if isinstance(entry, dict) and entry.get("task_id"):
                done.add(entry["task_id"])

    kept: list[dict] = []
    for inst in load_jsonl(args.inp):
        if args.limit and len(kept) >= args.limit:
            break
        tid = inst.get("task_id")
        if not tid or tid in done:
            continue
        try:
            analyzed = analyze_one(inst, data_dir=args.data_dir)
        except Exception as exc:  # noqa: BLE001
            append_drop(args.drop, tid, f"error: {exc}")
            done.add(tid)
            print(f"  drop {tid}: error: {exc}")
            continue
        if analyzed.get("_drop"):
            append_drop(args.drop, tid, analyzed["reason"])
            done.add(tid)
            print(f"  drop {tid}: {analyzed['reason']}")
            continue
        kept.append(analyzed)
        done.add(tid)
        print(f"  ok {tid}: intents={len(analyzed.get('intents') or [])}")

    existing = {r["task_id"]: r for r in load_jsonl(args.out) if r.get("task_id")}
    for r in kept:
        existing[r["task_id"]] = r
    write_jsonl(args.out, existing.values(), append=False)
    print(f"intent {len(kept)} new; total {len(existing)}; out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
