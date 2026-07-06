"""signals.py — turn raw loop traces into the signals the evolver learns from.

A loop trace is the record of one inner review loop (L4<->L3 or L5<->L3):

  {
    "task": str, "worker": "l4"|"l5",
    "rounds": [
       {"round": int, "diff": str, "worker_report": str,
        "l3_report": str, "approved": bool, "verdict": "pass"|"needs_work"|"fail",
        "comments": str, "pushed_back": bool, "l3_used_tools": bool}
    ],
    "approved": bool, "jury_convened": bool, "jury_pass": bool|None,
    "final_diff": str,
    "labels": {"gt_pass": bool|None, "reward_hack": bool, "false_block": bool,
               "source": str} | None,
  }

The signals below are deliberately the same ones the neighbouring worktrial mined,
mapped onto L4/L5 (coder) and L3 (reviewer):

  - responsiveness : mechanical — after L3 asks for changes, did the diff change?
  - alignment      : LLM-judged — did the change actually ADDRESS the ask?
  - calibration    : was L3's final verdict right vs ground truth (or the jury)?
  - batch patterns : systemic issues recurring across a batch (what the evolver
                     should fix in the BASE prompt, not one-offs).

All functions are pure given their inputs; alignment takes an injected `judge`
callable so this module never imports an LLM client (keeps it unit-testable and
free of the bench/training side effects the eval workers must avoid).
"""
from collections import Counter


def _round_diff(r):
    return (r.get("diff") or "").strip()


def _requested_changes(r):
    """True if this round's L3 review asked for more work (did not pass)."""
    return not r.get("approved")


def responsiveness(trace):
    """Mechanical: after each L3 change-request, did the diff change next round?
    score = changed_rounds / rounds_that_requested_changes."""
    rounds = trace.get("rounds", [])
    changed = requested = 0
    for i in range(len(rounds) - 1):
        if not _requested_changes(rounds[i]):
            continue
        requested += 1
        if _round_diff(rounds[i + 1]) != _round_diff(rounds[i]):
            changed += 1
    return {"score": (changed / requested) if requested else None,
            "changed": changed, "requested": requested}


def alignment(trace, judge=None):
    """LLM-judged: did each next diff move toward what L3 asked for?
    score = aligned_rounds / rounds_that_requested_changes.

    `judge(comments, before, after) -> bool` is injected (unanimous panel etc. is
    the caller's concern). A round whose diff did not change counts as not-aligned
    without spending a judge call. Returns score=None if there is no judge or no
    change was ever requested."""
    rounds = trace.get("rounds", [])
    if judge is None:
        return {"score": None, "aligned": 0, "requested": 0}
    aligned = requested = 0
    for i in range(len(rounds) - 1):
        if not _requested_changes(rounds[i]):
            continue
        requested += 1
        before, after = _round_diff(rounds[i]), _round_diff(rounds[i + 1])
        if after != before and judge(rounds[i].get("comments", ""), before, after):
            aligned += 1
    return {"score": (aligned / requested) if requested else None,
            "aligned": aligned, "requested": requested}


def trace_oracle(trace):
    """Return (passed: bool|None, source: str|None) — the best correctness signal.

    Real ground truth (hidden tests) wins; otherwise a trusted unanimous jury;
    otherwise unknown (None)."""
    labels = trace.get("labels")
    if labels and labels.get("gt_pass") is not None:
        return bool(labels["gt_pass"]), labels.get("source", "tests")
    if trace.get("jury_convened") and trace.get("jury_pass") is not None:
        return bool(trace["jury_pass"]), "jury"
    return None, None


def calibration(trace):
    """Was L3's final verdict calibrated against the correctness oracle?

    Returns 'too_lenient' (L3 approved but it fails), 'too_strict' (L3 blocked but
    it passes), 'calibrated', or None (no trustworthy signal)."""
    passed, source = trace_oracle(trace)
    if passed is None:
        return None
    approved = bool(trace.get("approved"))
    if approved and not passed:
        return "too_lenient"
    if not approved and passed:
        return "too_strict"
    return "calibrated"


def _round_is_silent(r):
    """L3 asked for changes but gave no substantive comment."""
    return _requested_changes(r) and not (r.get("comments") or "").strip()


def _round_is_rubber_stamp(r):
    """L3 approved without running any tool to check (first-glance approval)."""
    return r.get("approved") and r.get("l3_used_tools") is False


def batch_patterns(traces, judge=None, min_tasks=2, low_score=0.5):
    """Aggregate signals across a batch and flag what RECURS (>= min_tasks tasks).

    The evolver only sees one batch at a time; these recurring patterns are what
    it should fix in the BASE prompts rather than chasing single-task noise.
    Returns a list of {pattern, tasks, note}.
    """
    silent = rubber = reward_hack = false_block = low_resp = low_algn = 0
    comment_freq = Counter()
    for t in traces:
        rounds = t.get("rounds", [])
        if any(_round_is_silent(r) for r in rounds):
            silent += 1
        if any(_round_is_rubber_stamp(r) for r in rounds):
            rubber += 1
        labels = t.get("labels") or {}
        if labels.get("reward_hack"):
            reward_hack += 1
        if labels.get("false_block"):
            false_block += 1
        resp = responsiveness(t)["score"]
        if resp is not None and resp < low_score:
            low_resp += 1
        algn = alignment(t, judge)["score"]
        if algn is not None and algn < low_score:
            low_algn += 1
        for r in rounds:
            c = (r.get("comments") or "").strip()
            if c:
                comment_freq[c[:80]] += 1

    flagged = []

    def flag(count, name, note):
        if count >= min_tasks:
            flagged.append({"pattern": name, "tasks": count, "note": note})

    flag(silent, "reviewer_silence", "L3 asked for changes without saying what to fix.")
    flag(rubber, "reviewer_rubber_stamp", "L3 approved without running any check.")
    flag(reward_hack, "reward_hack", "L3 passed code the hidden tests reject.")
    flag(false_block, "false_block", "L3 blocked code the hidden tests accept.")
    flag(low_resp, "coder_unresponsive", "Implementer ignored L3 change-requests.")
    flag(low_algn, "coder_misaligned", "Implementer changed code but missed the ask.")
    for comment, k in comment_freq.most_common(5):
        if k >= min_tasks:
            flagged.append({"pattern": "repeated_comment", "tasks": k,
                            "note": f"L3 keeps repeating: {comment!r}"})
    return flagged
