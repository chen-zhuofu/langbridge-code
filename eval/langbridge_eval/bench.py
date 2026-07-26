"""bench.py — spec loading + candidate-diff helpers for Docker eval.

Specs are JSON under ``data/eval/specs/`` (symlink to ``data-pipeline/curate/out/``).
Grading for public e2e runs in-container via ``grade_checkout``.
"""
import json
import os
import re
from pathlib import Path

SPECS_DIR = os.environ.get("LANGBRIDGE_SPECS_DIR", "")
TEST_PREFIX = os.environ.get("LANGBRIDGE_TEST_PREFIX", "tests/")

# Agent / eval scaffolding that must never enter the candidate patch.
EVAL_NOISE_PREFIXES = (
    ".langbridge/",
    ".refvenv/",
    "agent-state/",
)
DIFF_EXCLUDE_PATHSPECS = tuple(f":!{prefix.rstrip('/')}" for prefix in EVAL_NOISE_PREFIXES)


def _diff_path(line: str) -> str | None:
    if not line.startswith("diff --git "):
        return None
    m = re.search(r" b/(\S+)", line)
    return m.group(1) if m else None


def is_eval_noise_path(path: str) -> bool:
    """True if path is agent/eval scaffolding, not candidate source."""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return any(
        normalized == p.rstrip("/") or normalized.startswith(p)
        for p in EVAL_NOISE_PREFIXES
    )


def strip_eval_noise(patch_text: str) -> str:
    """Drop hunks for agent runtime / venv / state dirs from a candidate diff."""
    out, keep = [], True
    for line in (patch_text or "").splitlines(keepends=True):
        path = _diff_path(line)
        if path is not None:
            keep = not is_eval_noise_path(path)
        if keep:
            out.append(line)
    return "".join(out)


def split_diff(patch_text, test_prefix=None):
    """Drop test-file hunks and eval noise from a candidate diff."""
    test_prefix = test_prefix or TEST_PREFIX
    out, keep = [], True
    for line in strip_eval_noise(patch_text).splitlines(keepends=True):
        path = _diff_path(line)
        if path is not None:
            keep = not path.startswith(test_prefix)
        if keep:
            out.append(line)
    return "".join(out)


def specs_dir() -> str:
    return SPECS_DIR or str(
        Path(__file__).resolve().parents[2] / "data" / "eval" / "specs"
    )


def list_specs(directory=None, ok_only=True, hard=None):
    """Return the loaded specs in the dir. Filter by status/hard if asked.

    Skips subdirectories and underscore-prefixed names (e.g. ``_excluded/``).
    """
    directory = directory or specs_dir()
    if not os.path.isdir(directory):
        return []
    out = []
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json") or fname.startswith("_"):
            continue
        if fname == "drop.json":
            continue
        path = os.path.join(directory, fname)
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            spec = json.load(f)
        if ok_only and spec.get("status") != "ok":
            continue
        if hard is not None and bool(spec.get("hard")) != hard:
            continue
        out.append(spec)
    return out
