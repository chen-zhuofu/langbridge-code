"""Split a unified diff into test vs non-test patches."""
from __future__ import annotations

import re
from pathlib import Path

_TEST_HINTS = (
    "/tests/",
    "/test/",
    "/__tests__/",
    "/e2e_tests/",
    "tests/",
)

_FILE_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$", re.MULTILINE)

_TEST_NAME_SUFFIXES = (
    "_test.py",
    "_test.go",
    "_test.ts",
    "_test.tsx",
    "_test.js",
    "_test.jsx",
    ".test.py",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
)


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lstrip("./")
    name = Path(p).name
    lower = p.lower()
    name_l = name.lower()
    if name_l == "conftest.py":
        return True
    if name_l.startswith("test_") and name_l.endswith((".py", ".go", ".ts", ".js")):
        return True
    if any(name_l.endswith(suf) for suf in _TEST_NAME_SUFFIXES):
        return True
    return any(h in lower for h in _TEST_HINTS)


def split_unified_diff(patch: str) -> tuple[str, str, list[str], list[str]]:
    """Return (test_patch, code_patch, test_files, code_files)."""
    if not patch or not patch.strip():
        return "", "", [], []

    chunks: list[tuple[str, str]] = []
    current_path = ""
    current_lines: list[str] = []
    for line in patch.splitlines(keepends=True):
        m = re.match(r"^diff --git a/(.+) b/(.+)$", line.rstrip("\n"))
        if m:
            if current_lines:
                chunks.append((current_path, "".join(current_lines)))
            current_path = m.group(2)
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append((current_path, "".join(current_lines)))

    test_parts: list[str] = []
    code_parts: list[str] = []
    test_files: list[str] = []
    code_files: list[str] = []
    for path, body in chunks:
        if is_test_path(path):
            test_parts.append(body)
            test_files.append(path)
        else:
            code_parts.append(body)
            code_files.append(path)
    return "".join(test_parts), "".join(code_parts), test_files, code_files


def changed_paths_from_diff(patch: str) -> list[str]:
    paths = []
    for m in _FILE_HEADER.finditer(patch or ""):
        paths.append(m.group(2))
    return paths


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def strip_paths_from_diff(patch: str, drop: set[str]) -> str:
    """Keep unified-diff hunks whose ``b/`` path is not in ``drop``."""
    if not patch or not patch.strip() or not drop:
        return patch or ""
    drop_norm = {_normalize_repo_path(p) for p in drop if p}
    out: list[str] = []
    keep = True
    for line in patch.splitlines(keepends=True):
        m = re.match(r"^diff --git a/(.+) b/(.+)$", line.rstrip("\n"))
        if m:
            keep = _normalize_repo_path(m.group(2)) not in drop_norm
        if keep:
            out.append(line)
    return "".join(out)


def code_only_for_grade(
    candidate_diff: str,
    *,
    test_patch: str = "",
    test_files: list[str] | None = None,
) -> str:
    """Drop agent test hunks so the official ``test_patch`` wins at grade time.

    Strips: paths touched by ``test_patch``, ``test_files``, and any path that
    looks like a test file. Non-test (product) hunks are kept.
    """
    protected = {
        _normalize_repo_path(p) for p in changed_paths_from_diff(test_patch or "")
    }
    protected.update(
        _normalize_repo_path(f) for f in (test_files or []) if f
    )
    _, code_patch, _, _ = split_unified_diff(candidate_diff or "")
    return strip_paths_from_diff(code_patch, protected)
