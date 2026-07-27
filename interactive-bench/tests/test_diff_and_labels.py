"""Tests for diff split and files_touched dirty check."""
from __future__ import annotations

import sys
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib.diff_split import is_test_path, split_unified_diff  # noqa: E402
from _lib.labels import difficulty_from_runtime, task_type_from_prompt_intents  # noqa: E402
from _lib.resolve_commits import files_outside_touched  # noqa: E402


SAMPLE = """diff --git a/src/foo.py b/src/foo.py
index 111..222 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1 +1 @@
-old
+new
diff --git a/tests/test_foo.py b/tests/test_foo.py
index 333..444 100644
--- a/tests/test_foo.py
+++ b/tests/test_foo.py
@@ -1 +1 @@
-assert False
+assert True
"""


def test_is_test_path():
    assert is_test_path("tests/test_foo.py")
    assert is_test_path("src/test_bar.py")
    assert is_test_path("cmd/cli/cleanup_test.go")
    assert is_test_path("src/foo.spec.ts")
    assert not is_test_path("src/foo.py")
    assert not is_test_path("cmd/cli/cleanup.go")


def test_split_unified_diff():
    test_patch, code_patch, test_files, code_files = split_unified_diff(SAMPLE)
    assert "tests/test_foo.py" in test_files
    assert "src/foo.py" in code_files
    assert "test_foo" in test_patch
    assert "src/foo.py" in code_patch


def test_files_outside_touched_dirty():
    dirty = files_outside_touched(
        ["src/foo.py", "tests/test_foo.py", "README.md"],
        ["src/foo.py", "tests/test_foo.py"],
    )
    assert dirty == ["README.md"]


def test_difficulty_buckets():
    assert difficulty_from_runtime(60) == "easy"
    assert difficulty_from_runtime(20 * 60) == "medium"
    assert difficulty_from_runtime(60 * 60) == "hard"
    assert difficulty_from_runtime(None) == "unknown"


def test_task_type_labels():
    assert task_type_from_prompt_intents(["debug"]) == "bug_fix"
    assert task_type_from_prompt_intents(["Create new code"]) == "feature"
    assert task_type_from_prompt_intents(["refactor"]) == "refactor"
