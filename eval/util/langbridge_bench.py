"""langbridge_bench.py — load the self-built langbridge-bench dataset.

Eval-ready specs live under ``eval/data/langbridge-bench/specs/``.
"""
import os
import re
from pathlib import Path

from util import bench

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|conftest\.py$",
    re.IGNORECASE,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SPECS = _REPO_ROOT / "eval" / "data" / "langbridge-bench" / "specs"
SPECS_DIR = os.environ.get("LANGBRIDGE_SPECS_DIR", str(_DEFAULT_SPECS))


def _strip_test_hunks(diff, test_files=None):
    test_files = set(test_files or [])
    out, keep = [], True
    for line in (diff or "").splitlines(keepends=True):
        if line.startswith("diff --git "):
            m = re.search(r" b/(\S+)", line)
            path = m.group(1) if m else ""
            keep = not (path in test_files or _TEST_PATH_RE.search(path))
        if keep:
            out.append(line)
    return "".join(out)


def specs(hard=None, directory=None):
    return bench.list_specs(directory=directory or SPECS_DIR, ok_only=True, hard=hard)
