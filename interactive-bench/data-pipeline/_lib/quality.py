"""Admission / quality gates for interactive-bench tasks (non-agent)."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

# Very common English / chat words — not useful for oracle↔instruction overlap.
_STOP = frozenset(
    """
    the and for with that this from into your you are was were will can not
    please also just like make sure need needs add fix update change create
    implement write using used use based about after before then than when
    where what which while should would could must may might into onto over
    under between among into file files code test tests please thanks hello
    hi ok okay yes no new old more less first next last all any some such
    feature bug issue problem plan design doc docs readme commit push remote
    folder folders project via api env default left right button mode
    """.split()
)

_IMAGE_RE = re.compile(r"\[Image:\s*[^\]]*\]", re.IGNORECASE)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_META_INTENT_RE = re.compile(
    r"(?i)^\s*("
    r"commit(\s+the\s+(restructured\s+)?changes?)?"
    r"|push(\s+the\s+(committed\s+)?changes?)?(\s+to\s+(the\s+)?remote)?"
    r"|create\s+a\s+(pull\s+request|pr)"
    r"|open\s+a\s+(pull\s+request|pr)"
    r"|take\s+a\s+screenshot"
    r"|look\s+at\s+(the\s+)?(attached\s+)?screenshot"
    r")\b"
)
_COMMAND_ARGS_RE = re.compile(
    r"<command-args>(.*?)</command-args>", re.IGNORECASE | re.DOTALL
)
_INTERRUPTED_RE = re.compile(r"(?i)\[Request interrupted[^\]]*\]")
_SKILL_DUMP_RE = re.compile(r"(?i)^\s*Base directory for this skill\s*:")

# Prefer short windows: long base→gold spans pull in unrelated tip tests.
MAX_WINDOW_COMMITS = 15
MAX_CODE_FILES = 40
MAX_INTENTS = 12
MIN_ORACLE_TOKEN_HITS = 1
MIN_INSTRUCTION_CHARS = 24
# Window score needs real oracle hits (100 pts each); path-only noise is not enough.
MIN_WINDOW_SCORE = 100


def clean_user_text(text: str | None) -> str:
    """Strip chat media placeholders and collapse whitespace."""
    if not text:
        return ""
    out = _IMAGE_RE.sub("", str(text))
    out = _MD_IMAGE_RE.sub("", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def normalize_user_prompt(text: str | None) -> str:
    """Clean user text and unwrap skill/command chrome when possible.

    Claude Code sessions often wrap the real ask in ``<command-args>``. Prefer
    that body over the slash-command shell so intents stay coding-relevant.
    """
    out = clean_user_text(text)
    if not out:
        return ""
    match = _COMMAND_ARGS_RE.search(out)
    if match:
        args = clean_user_text(match.group(1))
        if args:
            return args
    return out


def is_meta_intent(text: str | None) -> bool:
    """True for commit/push/screenshot-only intents (not coding work)."""
    if not text or not str(text).strip():
        return True
    return bool(_META_INTENT_RE.match(str(text).strip()))


def is_noise_user_message(text: str | None) -> bool:
    """True for chat chrome that should not become coding intents.

    Covers commit/push meta, interrupted stubs, skill dumps, subagent
    task-notifications, and bare slash-command wrappers with no real ask.
    """
    raw = normalize_user_prompt(text)
    if not raw:
        return True
    if is_meta_intent(raw):
        return True
    if _SKILL_DUMP_RE.match(raw):
        return True
    low = raw.lower()
    if "<task-notification>" in low:
        return True
    interrupted_only = _INTERRUPTED_RE.sub("", raw).strip()
    if _INTERRUPTED_RE.search(raw) and len(interrupted_only) < MIN_INSTRUCTION_CHARS:
        return True
    # Bare /skill invoke without command-args is chrome, not a coding ask.
    original = clean_user_text(text)
    if "<command-message>" in original.lower() or "<command-name>" in original.lower():
        if _COMMAND_ARGS_RE.search(original):
            return False
        return True
    return False


def filter_intents(intents: Iterable[dict[str, Any]], *, max_intents: int = MAX_INTENTS) -> list[dict]:
    """Drop noise/meta intents, renumber ids, keep at most ``max_intents``."""
    kept: list[dict] = []
    for item in intents:
        if not isinstance(item, dict):
            continue
        original = str(item.get("text") or "")
        if is_noise_user_message(original):
            continue
        text = normalize_user_prompt(original)
        if not text or is_noise_user_message(text):
            continue
        kept.append({**item, "text": text})
        if len(kept) >= max_intents:
            break
    if not kept:
        return []
    # Ensure exactly one revealed_at_start (first kept).
    out = []
    for index, item in enumerate(kept):
        out.append(
            {
                "id": f"i{index + 1}",
                "text": item["text"],
                "source_turn": int(item.get("source_turn") or index),
                "revealed_at_start": index == 0,
            }
        )
    return out


# Generic repo/framework words that often leak into both chat and tests.
_WEAK_ORACLE = frozenset(
    """
    backend frontend tests test app src api client server module package session
    router route routes status token async await import export function class
    method size data file files http json error message messages request response
    config settings utils helper helpers main index type types value values name
    names get set post put delete patch call calls only needed manually finally
    invalid endpoint dependencies fastapi django flask pytest unittest model
    models service services manager managers user users auth login page pages
    component components button buttons state store stores hook hooks context
    readme docs doc design plan feature bug issue task job jobs process
    version versions commit commits change changes remote repository git
    """.split()
)


def significant_tokens(text: str) -> set[str]:
    # Split on non-alnum so ``test_checkpoint_saves`` yields checkpoint, saves, …
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", (text or "").lower())
    return {t for t in raw if t not in _STOP and not t.isdigit()}


def strong_oracle_hits(hits: set[str]) -> set[str]:
    return {h for h in hits if h not in _WEAK_ORACLE and len(h) >= 5}


def oracle_token_hits(
    *,
    instruction: str,
    intents: list[dict] | None,
    test_files: list[str] | None,
    fail_to_pass: list[str] | None,
    test_patch: str | None,
) -> set[str]:
    """Tokens from the user-facing task that also appear in the test oracle."""
    parts = [instruction or ""]
    for intent in intents or []:
        parts.append(str(intent.get("text") or ""))
    task_tokens = significant_tokens(" ".join(parts))
    oracle_bits = [
        " ".join(test_files or []),
        " ".join(fail_to_pass or []),
        (test_patch or "")[:80_000],
    ]
    oracle_tokens = significant_tokens(" ".join(oracle_bits))
    return strong_oracle_hits(task_tokens & oracle_tokens)


def oracle_aligned(
    *,
    instruction: str,
    intents: list[dict] | None,
    test_files: list[str] | None,
    fail_to_pass: list[str] | None,
    test_patch: str | None,
    min_hits: int = MIN_ORACLE_TOKEN_HITS,
) -> bool:
    return (
        len(
            oracle_token_hits(
                instruction=instruction,
                intents=intents,
                test_files=test_files,
                fail_to_pass=fail_to_pass,
                test_patch=test_patch,
            )
        )
        >= min_hits
    )


def f2p_fingerprint(fail_to_pass: Iterable[str], *, repo: str = "") -> str:
    body = "\n".join(sorted(str(t) for t in fail_to_pass))
    raw = f"{repo}\n{body}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def has_python_tests(test_files: Iterable[str]) -> bool:
    return any(str(f).endswith(".py") for f in test_files)


def is_thin_instruction(text: str | None) -> bool:
    cleaned = normalize_user_prompt(text)
    if not cleaned:
        return True
    if is_noise_user_message(text) or is_noise_user_message(cleaned):
        return True
    # Short openers ("ok", "1 GB" after image strip) are not usable task statements.
    if len(cleaned) < MIN_INSTRUCTION_CHARS:
        return True
    if not significant_tokens(cleaned):
        return True
    return False


def pick_instruction(prompts: Iterable[str], *, fallback: str = "") -> tuple[str, list[str]]:
    """Pick the first substantive user prompt; return (instruction, followups)."""
    cleaned = [normalize_user_prompt(p) for p in prompts]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        fb = normalize_user_prompt(fallback)
        return fb, []
    for index, text in enumerate(cleaned):
        if is_thin_instruction(text):
            continue
        # Follow-ups stay normalized; drop trailing noise later in curate (intent LLM).
        return text, cleaned[index + 1 :]
    # Fall back to longest non-empty prompt if all look thin.
    best = max(cleaned, key=len)
    idx = cleaned.index(best)
    return best, cleaned[idx + 1 :]


def path_token_overlap(paths_a: Iterable[str], paths_b: Iterable[str]) -> set[str]:
    """Strong path-stem overlap (e.g. embedding in tests/test_embedding.py)."""
    a = significant_tokens(" ".join(str(p) for p in paths_a))
    b = significant_tokens(" ".join(str(p) for p in paths_b))
    return strong_oracle_hits(a & b)


def score_testful_window(
    *,
    instruction: str,
    followups: list[str] | None,
    files_touched: list[str] | None,
    test_files: list[str],
    code_files: list[str],
    test_patch: str,
    window_commits: int,
) -> int:
    """Higher is better. Used to pick among candidate base→gold suffixes."""
    intents = [
        {"text": t}
        for t in ([instruction] + list(followups or []))[:8]
        if t and not is_noise_user_message(t)
    ]
    hits = oracle_token_hits(
        instruction=instruction,
        intents=intents,
        test_files=test_files,
        fail_to_pass=[],
        test_patch=test_patch,
    )
    touched = list(files_touched or [])
    path_hits = path_token_overlap(touched or code_files, test_files)
    code_test_hits = path_token_overlap(code_files, test_files)
    return (
        100 * len(hits)
        + 40 * len(path_hits)
        + 20 * len(code_test_hits)
        - 2 * len(code_files)
        - window_commits
    )


def test_patch_fingerprint(test_patch: str, *, repo: str = "") -> str:
    raw = f"{repo}\n{(test_patch or '').strip()}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()
