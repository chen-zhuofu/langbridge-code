"""LLM classification of curated tasks: type + difficulty (uses F2P).

TODO(difficulty): Today's labels come from one LLM reading the problem
statement + F2P/P2P names. Replace (or calibrate) that with multi-model
solve attempts — run several models on the task and derive difficulty from
pass/fail / attempt cost, not statement heuristics alone.
"""
from __future__ import annotations

import json
import re
from typing import Any

TASK_TYPES = ("bug_fix", "feature", "refactor", "unknown")
DIFFICULTIES = ("easy", "medium", "hard", "unknown")

DEFAULT_TASK_TYPE = "unknown"
DEFAULT_DIFFICULTY = "unknown"
DEFAULT_UNKNOWN_REASON = "could not decide"

CLASSIFY_SYSTEM = """\
You classify one coding-agent eval task for a benchmark dataset.

You see the final problem statement (after curation), changed-file count when
known, and FAIL_TO_PASS / PASS_TO_PASS test names from the reference harness.
Assign:

1. task_type — exactly one of:
   - bug_fix: restore broken/incorrect behavior
   - feature: add new capability or API
   - refactor: restructure/cleanup with behavior intended to stay the same
     (includes renames, extract helpers, type cleanups without new user-facing
     behavior)
   - unknown: cannot tell from the statement and tests
     (explain why in task_type_reason)

2. difficulty — exactly one of:
   - easy: localized change, clear expected behavior, few files; typically
     1 FAIL_TO_PASS test
   - medium: moderate scope or non-obvious diagnosis, still one coherent task;
     often a small handful of FAIL_TO_PASS tests
   - hard: multi-file / subtle interaction / ambiguous edge cases / broad
     behavioral change; often several FAIL_TO_PASS tests or tightly coupled
     suites
   - unknown: cannot place difficulty confidently
     (explain why in difficulty_reason)

You may mark task_type and difficulty independently (e.g. type known,
difficulty unknown). Prefer a concrete label when evidence is clear; use
unknown rather than guessing.

Weigh both the statement and FAIL_TO_PASS: more failing tests and cross-cutting
names push difficulty up, but a single deep/subtle test can still be hard.
Do not ignore a clearly hard statement just because only one test failed.

Reply with ONLY a JSON object (no markdown fences):
{
  "task_type": "bug_fix" | "feature" | "refactor" | "unknown",
  "task_type_reason": "one short sentence explaining the task_type label",
  "difficulty": "easy" | "medium" | "hard" | "unknown",
  "difficulty_reason": "one short sentence explaining the difficulty label"
}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _normalize_label(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _parse_classification(text: str) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty classify response")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_RE.search(raw)
        if not match:
            raise ValueError(f"no JSON object in classify response: {raw[:200]!r}")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("classify response is not an object")

    # Accept legacy "task_kind" from older prompts/responses.
    task_type = _normalize_label(data.get("task_type") or data.get("task_kind"))
    if task_type in ("bugfix", "bug"):
        task_type = "bug_fix"
    if task_type in ("unkown", "n/a", "na", "none"):
        task_type = "unknown"
    if task_type not in TASK_TYPES:
        raise ValueError(f"invalid task_type: {task_type!r}")

    difficulty = _normalize_label(data.get("difficulty"))
    if difficulty in ("unkown", "n/a", "na", "none"):
        difficulty = "unknown"
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"invalid difficulty: {difficulty!r}")

    type_reason = str(
        data.get("task_type_reason") or data.get("reason") or ""
    ).strip()
    difficulty_reason = str(
        data.get("difficulty_reason") or data.get("reason") or ""
    ).strip()
    if not type_reason:
        if task_type == "unknown":
            type_reason = DEFAULT_UNKNOWN_REASON
        else:
            raise ValueError("classify response missing task_type_reason")
    if not difficulty_reason:
        if difficulty == "unknown":
            difficulty_reason = DEFAULT_UNKNOWN_REASON
        else:
            raise ValueError("classify response missing difficulty_reason")

    return {
        "task_type": task_type,
        "task_type_reason": type_reason,
        "difficulty": difficulty,
        "difficulty_reason": difficulty_reason,
    }


def _num_files(instance: dict[str, Any]) -> Any:
    if instance.get("_num_files") is not None:
        return instance["_num_files"]
    meta = instance.get("metadata") or {}
    return meta.get("num_files")


def _user_payload(instance: dict[str, Any]) -> str:
    f2p = list(
        instance.get("FAIL_TO_PASS")
        or instance.get("fail_to_pass")
        or []
    )
    p2p = list(
        instance.get("PASS_TO_PASS")
        or instance.get("pass_to_pass")
        or []
    )
    payload = {
        "task_id": instance.get("task_id") or instance.get("instance_id"),
        "repo": instance.get("repo"),
        "num_files": _num_files(instance),
        "problem_statement": (instance.get("problem_statement") or "")[:12_000],
        "fail_to_pass_count": len(f2p),
        "fail_to_pass_names": f2p[:40],
        "pass_to_pass_count": len(p2p),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def classify_instance(
    instance: dict[str, Any],
    *,
    api_key: str,
    model: str,
    label: str = "curate-classify",
) -> dict[str, str]:
    """Return task_type / difficulty and separate reasons for one instance.

    TODO(difficulty): label difficulty via multi-model solve attempts later;
    do not keep relying only on LLM reading the statement + F2P names.
    """
    from langbridge_code.llm.client import create_model_response
    from langbridge_code.llm.parse import extract_output_text

    response = create_model_response(
        api_key,
        model,
        [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": _user_payload(instance)},
        ],
        label=label,
    )
    text = extract_output_text(response.get("output", []) or [])
    return _parse_classification(text or "")


def apply_classification(
    instance: dict[str, Any],
    classification: dict[str, str] | None,
) -> dict[str, Any]:
    """Mutate instance with classification fields (unknown if undecided)."""
    if classification:
        task_type = classification.get("task_type") or DEFAULT_TASK_TYPE
        difficulty = classification.get("difficulty") or DEFAULT_DIFFICULTY
        type_reason = (
            classification.get("task_type_reason") or DEFAULT_UNKNOWN_REASON
        )
        difficulty_reason = (
            classification.get("difficulty_reason") or DEFAULT_UNKNOWN_REASON
        )
    else:
        task_type = DEFAULT_TASK_TYPE
        difficulty = DEFAULT_DIFFICULTY
        type_reason = DEFAULT_UNKNOWN_REASON
        difficulty_reason = DEFAULT_UNKNOWN_REASON
    if task_type not in TASK_TYPES:
        task_type = DEFAULT_TASK_TYPE
    if difficulty not in DIFFICULTIES:
        difficulty = DEFAULT_DIFFICULTY

    instance["task_type"] = task_type
    instance["difficulty"] = difficulty
    instance["task_type_reason"] = type_reason
    instance["difficulty_reason"] = difficulty_reason
    instance["hard"] = difficulty == "hard"
    instance.pop("task_kind", None)

    meta = instance.get("metadata")
    if isinstance(meta, dict):
        meta.pop("classify_reason", None)

    return instance
