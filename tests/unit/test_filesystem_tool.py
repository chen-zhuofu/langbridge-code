import json

import pytest

from langbridge_cli.tools import TOOL_SCHEMAS, TOOLS
from langbridge_cli.tools.filesystem import delete_file, glob, grep


def test_grep_and_glob_are_registered():
    assert "glob" in TOOLS
    assert "grep" in TOOLS
    names = {schema["name"] for schema in TOOL_SCHEMAS}
    assert {"glob", "grep"}.issubset(names)


@pytest.mark.skipif(__import__("shutil").which("rg") is None, reason="ripgrep not installed")
def test_glob_finds_files(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    (tmp_path / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("y = 2\n", encoding="utf-8")

    payload = json.loads(glob("*.py", path="."))

    assert payload["matches"] == ["alpha.py"]


@pytest.mark.skipif(__import__("shutil").which("rg") is None, reason="ripgrep not installed")
def test_grep_finds_content(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    (tmp_path / "sample.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    payload = json.loads(grep("hello", path=".", output_mode="content"))

    assert payload["matches"][0]["path"] == "sample.py"
    assert payload["matches"][0]["line"] == 1
    assert "def hello" in payload["matches"][0]["text"]


def test_delete_file_is_registered():
    assert "delete_file" in TOOLS
    assert any(schema["name"] == "delete_file" for schema in TOOL_SCHEMAS)


def test_delete_file_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    target = tmp_path / "stale.txt"
    target.write_text("remove me", encoding="utf-8")

    result = delete_file("stale.txt")

    assert result == "Deleted stale.txt."
    assert not target.exists()


def test_delete_file_rejects_directories(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    (tmp_path / "folder").mkdir()

    with pytest.raises(IsADirectoryError, match="Not a file"):
        delete_file("folder")
