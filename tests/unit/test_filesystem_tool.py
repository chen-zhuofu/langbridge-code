import json

import pytest

from langbridge_code.agents.common.workspace import set_workspace_root
from langbridge_code.tools import TOOL_SCHEMAS, TOOLS
from langbridge_code.tools.filesystem import delete_file, glob, grep, read_file


@pytest.fixture
def isolated_workspace(tmp_path):
    set_workspace_root(tmp_path)
    yield tmp_path
    set_workspace_root(None)


def test_grep_and_glob_are_registered():
    assert "glob" in TOOLS
    assert "grep" in TOOLS
    names = {schema["name"] for schema in TOOL_SCHEMAS}
    assert {"glob", "grep"}.issubset(names)


@pytest.mark.skipif(__import__("shutil").which("rg") is None, reason="ripgrep not installed")
def test_glob_finds_files(isolated_workspace):
    (isolated_workspace / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    (isolated_workspace / "beta.txt").write_text("y = 2\n", encoding="utf-8")

    payload = json.loads(glob("*.py", path="."))

    assert payload["matches"] == ["alpha.py"]


@pytest.mark.skipif(__import__("shutil").which("rg") is None, reason="ripgrep not installed")
def test_grep_finds_content(isolated_workspace):
    (isolated_workspace / "sample.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    output = grep("hello", path=".", output_mode="content")

    assert "sample.py" in output
    assert "def hello" in output


@pytest.mark.skipif(__import__("shutil").which("rg") is None, reason="ripgrep not installed")
def test_grep_single_file_with_trailing_colon(isolated_workspace):
    (isolated_workspace / "core.py").write_text(
        "def _calculate_separability_matrix(self):\n    return 1\n",
        encoding="utf-8",
    )

    output = grep("_calculate_separability_matrix", path="core.py", output_mode="content")

    assert "def _calculate_separability_matrix(self):" in output
    assert "Tool error" not in output


def test_write_overwrites_existing_file(isolated_workspace):
    from langbridge_code.tools.filesystem import write

    target = isolated_workspace / "sample.txt"
    target.write_text("old", encoding="utf-8")

    result = write("sample.txt", "new content")

    assert "overwrote" in result
    assert target.read_text(encoding="utf-8") == "new content"


def test_write_creates_new_file(isolated_workspace):
    from langbridge_code.tools.filesystem import write

    result = write("new.txt", "hello")

    assert result == "Wrote new.txt."
    assert (isolated_workspace / "new.txt").read_text(encoding="utf-8") == "hello"


def test_read_many_reads_multiple_files(isolated_workspace):
    from langbridge_code.tools.filesystem import read_many

    (isolated_workspace / "a.txt").write_text("A", encoding="utf-8")
    (isolated_workspace / "b.txt").write_text("B", encoding="utf-8")
    payload = json.loads(read_many(["a.txt", "b.txt"]))

    assert len(payload["files"]) == 2
    assert "1\tA" in payload["files"][0]["content"]


def test_multi_edit_applies_ordered_replacements(isolated_workspace):
    from langbridge_code.tools.filesystem import multi_edit

    (isolated_workspace / "sample.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    result = multi_edit(
        "sample.py",
        [{"old_string": "a = 1", "new_string": "a = 10"}, {"old_string": "b = 2", "new_string": "b = 20"}],
    )

    assert "2 replacement" in result
    assert (isolated_workspace / "sample.py").read_text(encoding="utf-8") == "a = 10\nb = 20\n"


def test_apply_patch_updates_file(isolated_workspace):
    from langbridge_code.tools.filesystem import apply_patch

    (isolated_workspace / "sample.py").write_text("line1\nline2\n", encoding="utf-8")
    patch = """--- a/sample.py
+++ b/sample.py
@@ -1,2 +1,2 @@
 line1
-line2
+line2-updated
"""
    result = apply_patch(patch)

    assert "Applied patch" in result
    assert (isolated_workspace / "sample.py").read_text(encoding="utf-8") == "line1\nline2-updated\n"


def test_write_is_registered():
    assert "write" in TOOLS
    names = {schema["name"] for schema in TOOL_SCHEMAS}
    assert {"write", "read_many", "multi_edit", "apply_patch"}.issubset(names)


def test_delete_file_removes_file(isolated_workspace):
    target = isolated_workspace / "stale.txt"
    target.write_text("remove me", encoding="utf-8")

    result = delete_file("stale.txt")

    assert result == "Deleted stale.txt."
    assert not target.exists()


def test_delete_file_rejects_directories(isolated_workspace):
    (isolated_workspace / "folder").mkdir()

    with pytest.raises(IsADirectoryError, match="Not a file"):
        delete_file("folder")


def test_read_file_line_range(isolated_workspace):
    (isolated_workspace / "sample.py").write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    output = read_file("sample.py", offset=2, limit=2)

    assert "# sample.py lines 2-3 (4 lines total)" in output
    assert "2\tline2" in output
    assert "3\tline3" in output
    assert "line4" not in output


def test_read_file_legacy_start_end_lines(isolated_workspace):
    (isolated_workspace / "sample.py").write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    output = read_file("sample.py", start_line=2, end_line=3)

    assert "# sample.py lines 2-3 (4 lines total)" in output
    assert "2\tline2" in output


def test_read_file_large_file_offset(isolated_workspace):
    lines = [f"line {index}" for index in range(1, 1001)]
    lines[807] = "    def _calculate_separability_matrix(self):"
    (isolated_workspace / "core.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    output = read_file("core.py", offset=808, limit=5)

    assert "# core.py lines 808-812 (1000 lines total)" in output
    assert "_calculate_separability_matrix" in output


def test_read_file_by_function_name(isolated_workspace):
    (isolated_workspace / "sample.py").write_text(
        "def helper():\n    return 1\n\ndef target():\n    x = 2\n    return x\n",
        encoding="utf-8",
    )

    output = read_file("sample.py", function_name="target")

    assert "function `target`" in output
    assert "def target():" in output
    assert "return x" in output
    assert "def helper" not in output
