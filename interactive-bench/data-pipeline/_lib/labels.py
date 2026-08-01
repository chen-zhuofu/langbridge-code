"""Horizon (duration) and difficulty (complexity) buckets."""
from __future__ import annotations

from .diff_split import changed_paths_from_diff

# Horizon: how long the original human↔agent session ran. Fixed thresholds
# (seconds); tunable later from corpus percentiles.
SHORT_MAX = 10 * 60
MEDIUM_MAX = 30 * 60


def horizon_from_runtime(agent_runtime_sec: float | None) -> str:
    """Bucket by original agent runtime — a proxy for how long-horizon the task is."""
    if agent_runtime_sec is None:
        return "unknown"
    if agent_runtime_sec <= SHORT_MAX:
        return "short"
    if agent_runtime_sec <= MEDIUM_MAX:
        return "medium"
    return "long"


# Difficulty: size of the gold fix + how many tests it must flip. Sanity-
# checked against the first curated batch (7 specs, files/LOC/F2P spanned
# 0-26 / 0-4198 / 1-40) so the split isn't degenerate; still coarse — retune
# from corpus percentiles once there's a bigger batch.
FILES_EASY_MAX = 5
FILES_MEDIUM_MAX = 15
LOC_EASY_MAX = 100
LOC_MEDIUM_MAX = 300
F2P_EASY_MAX = 3
F2P_MEDIUM_MAX = 10

_DIFFICULTY_LABELS = ("easy", "medium", "hard")


def _patch_loc_changed(patch: str | None) -> int:
    """Count added+removed lines in a unified diff (excludes ``+++``/``---`` headers)."""
    if not patch:
        return 0
    count = 0
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            count += 1
    return count


def _bucket(value: int, easy_max: int, medium_max: int) -> int:
    if value <= easy_max:
        return 0
    if value <= medium_max:
        return 1
    return 2


def difficulty_from_complexity(
    code_patch: str | None,
    fail_to_pass: list[str] | None,
) -> str:
    """Bucket task difficulty from gold-patch size + FAIL_TO_PASS count.

    Files-changed and LOC-changed are both derived from ``code_patch`` itself
    (the gold non-test diff) so this needs no separate file-list input and can
    be recomputed from just what's already stored in a spec.

    Conservative "any red flag" rule: the overall bucket is the worst of the
    three signals — a wide blast radius or a large FAIL_TO_PASS count each
    independently signal a harder task even when the other signals look small.
    """
    if not code_patch and not fail_to_pass:
        return "unknown"
    files_score = _bucket(len(changed_paths_from_diff(code_patch)), FILES_EASY_MAX, FILES_MEDIUM_MAX)
    loc_score = _bucket(_patch_loc_changed(code_patch), LOC_EASY_MAX, LOC_MEDIUM_MAX)
    f2p_score = _bucket(len(fail_to_pass or []), F2P_EASY_MAX, F2P_MEDIUM_MAX)
    return _DIFFICULTY_LABELS[max(files_score, loc_score, f2p_score)]


def task_type_from_prompt_intents(intents: list[str] | None) -> str:
    """Map SWE-Chat prompt_intent labels → our task_type."""
    if not intents:
        return "other"
    labels = {str(x).lower() for x in intents if x}
    if "debug" in labels:
        return "bug_fix"
    if "refactor" in labels:
        return "refactor"
    if "create new code" in labels:
        return "feature"
    return "other"
