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
from _lib.labels import (  # noqa: E402
    difficulty_from_complexity,
    horizon_from_runtime,
    task_type_from_prompt_intents,
)
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


def test_horizon_buckets():
    assert horizon_from_runtime(60) == "short"
    assert horizon_from_runtime(20 * 60) == "medium"
    assert horizon_from_runtime(60 * 60) == "long"
    assert horizon_from_runtime(None) == "unknown"


def _diff_for(*paths: str) -> str:
    return "".join(f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n" for p in paths)


def test_difficulty_buckets_from_complexity():
    assert difficulty_from_complexity(None, None) == "unknown"
    assert difficulty_from_complexity(_diff_for("a.py"), ["t::a"]) == "easy"
    # 8 files (> FILES_EASY_MAX=5, <= FILES_MEDIUM_MAX=15) is the only red flag.
    assert difficulty_from_complexity(_diff_for(*[f"f{i}.py" for i in range(8)]), ["t::a"]) == "medium"
    # 11 F2P tests (> F2P_MEDIUM_MAX=10) is the only red flag.
    assert difficulty_from_complexity(_diff_for("a.py"), [f"t::{i}" for i in range(11)]) == "hard"
    # 350 changed lines (> LOC_MEDIUM_MAX=300) is the only red flag.
    big_diff = "diff --git a/a.py b/a.py\n" + "\n".join(f"+line{i}" for i in range(350))
    assert difficulty_from_complexity(big_diff, ["t::a"]) == "hard"


def test_task_type_labels():
    assert task_type_from_prompt_intents(["debug"]) == "bug_fix"
    assert task_type_from_prompt_intents(["Create new code"]) == "feature"
    assert task_type_from_prompt_intents(["refactor"]) == "refactor"
