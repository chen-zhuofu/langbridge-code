"""Interactive task spec shape (SWE-Chat sourced)."""
from __future__ import annotations

from typing import Any

from . import paths
from .runtime import eval_timeout_sec


def build_interactive_spec(inst: dict[str, Any]) -> dict[str, Any]:
    """Normalize a resolved/enriched instance into an eval-facing spec dict."""
    task_id = inst.get("task_id") or inst["session_id"]
    intents = inst.get("intents") or []
    runtime = inst.get("agent_runtime_sec")
    spec: dict[str, Any] = {
        "task_id": task_id,
        "source": "swe-chat",
        "session_id": inst["session_id"],
        "repo": inst["repo"],
        "base_commit": inst["base_commit"],
        "gold_commit": inst["gold_commit"],
        "instruction": inst.get("instruction") or inst.get("problem_statement") or "",
        "intents": list(intents),
        "baseline": {
            "prompt_count": inst.get("prompt_count"),
            "agent_runtime_sec": runtime,
            "user_persona": inst.get("user_persona"),
        },
        "sim": {
            "actions": ["no-op", "steer", "reveal_next", "answer"],
            "noop_message": "continue",
            "max_consecutive_noops": 4,
            "timeout_sec": eval_timeout_sec(runtime),
            "session_analysis": inst.get("session_analysis") or "",
        },
        "fail_to_pass": list(inst.get("fail_to_pass") or inst.get("FAIL_TO_PASS") or []),
        "pass_to_pass": list(inst.get("pass_to_pass") or inst.get("PASS_TO_PASS") or []),
        "test_patch": inst.get("test_patch") or "",
        "gold_code_patch": inst.get("gold_code_patch") or inst.get("patch") or "",
        "test_files": list(inst.get("test_files") or []),
        "docker_image": inst.get("docker_image") or paths.task_image(task_id),
        "task_type": inst.get("task_type") or "other",
        "difficulty": inst.get("difficulty") or "unknown",
        "horizon": inst.get("horizon") or "unknown",
    }
    meta = dict(inst.get("metadata") or {})
    for key in (
        "canonical_checkpoint_pk",
        "checkpoint_ids",
        "files_touched",
        "branch",
        "agent",
        "intent_model",
    ):
        if inst.get(key) is not None:
            meta.setdefault(key, inst[key])
    if meta:
        spec["metadata"] = meta
    return spec
