"""Misc helpers: conversations, dockerfile render, github compare helpers shape."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib.conversations import first_user_prompt, user_prompts_from_turns  # noqa: E402
from _lib.docker_util import render_python_dockerfile  # noqa: E402
from _lib import paths  # noqa: E402
from _lib.diff_split import changed_paths_from_diff  # noqa: E402
from _lib.github import is_ancestor_api  # noqa: E402


def test_user_prompts_from_turns_order():
    turns = [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": " first "},
        {"speaker": "human", "text": "second"},
        {"role": "user", "content": ""},
    ]
    assert user_prompts_from_turns(turns) == ["first", "second"]
    assert first_user_prompt(turns) == "first"
    assert first_user_prompt([], fallback="x") == "x"


def test_render_dockerfile_contains_repo_and_commit():
    text = render_python_dockerfile("org/repo", "abc123deadbeef")
    assert "org/repo" in text
    assert "abc123deadbeef" in text
    assert "FROM langbridge-bench:py312" in text
    assert paths.task_image("org__repo__abcd") == "lb-interactive:org__repo__abcd"


def test_changed_paths_from_diff():
    patch = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-x
+y
diff --git a/tests/t.py b/tests/t.py
--- a/tests/t.py
+++ b/tests/t.py
@@ -1 +1 @@
-a
+b
"""
    assert changed_paths_from_diff(patch) == ["a.py", "tests/t.py"]


def test_is_ancestor_api_uses_compare_status():
    with patch(
        "_lib.github._compare",
        return_value={"status": "ahead"},
    ):
        assert is_ancestor_api("org/repo", "sha", "tip") is True
    with patch(
        "_lib.github._compare",
        return_value={"status": "diverged"},
    ):
        assert is_ancestor_api("org/repo", "sha", "tip") is False
    assert is_ancestor_api("org/repo", "same", "same") is True
