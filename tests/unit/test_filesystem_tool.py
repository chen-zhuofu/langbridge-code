import json

import pytest

from langbridge_cli.tools import TOOL_SCHEMAS, TOOLS, main_tool_schemas, tool_schemas
from langbridge_cli.tools.profiles import detect_tool_profile
from langbridge_cli.tools.search import kimi_glob, kimi_grep, openai_glob_file_search, openai_grep_files


def test_openai_search_tools_are_registered():
    openai_schemas = tool_schemas(profile="openai")
    assert "grep_files" in TOOLS
    assert "glob_file_search" in TOOLS
    names = {schema["name"] for schema in openai_schemas}
    assert {"grep_files", "glob_file_search"}.issubset(names)


def test_kimi_search_tools_are_registered():
    schemas = tool_schemas(profile="kimi")
    names = {schema["name"] for schema in schemas}
    assert {"Grep", "Glob"}.issubset(names)
    assert "Grep" in TOOLS
    assert "Glob" in TOOLS


def test_profile_detection():
    assert detect_tool_profile(provider="moonshot", model="gpt-4") == "kimi"
    assert detect_tool_profile(provider="openai", model="kimi-k2") == "kimi"
    assert detect_tool_profile(provider="openai", model="gpt-4.1") == "openai"


def test_main_tool_schemas_switch_with_profile():
    openai_names = [schema["name"] for schema in main_tool_schemas(profile="openai")]
    kimi_names = [schema["name"] for schema in main_tool_schemas(profile="kimi")]
    assert openai_names == [
        "list_dir",
        "glob_file_search",
        "read_file",
        "grep_files",
        "execute_program",
        "read_webpage",
        "ask_l4_engineer",
        "ask_l5_engineer",
        "update_plan",
    ]
    assert kimi_names == [
        "list_dir",
        "Glob",
        "read_file",
        "Grep",
        "execute_program",
        "read_webpage",
        "ask_l4_engineer",
        "ask_l5_engineer",
        "update_plan",
    ]


@pytest.mark.skipif(__import__("shutil").which("rg") is None, reason="ripgrep not installed")
def test_openai_glob_finds_files(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    (tmp_path / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("y = 2\n", encoding="utf-8")

    payload = json.loads(openai_glob_file_search("*.py", path="."))

    assert payload["matches"] == ["alpha.py"]


@pytest.mark.skipif(__import__("shutil").which("rg") is None, reason="ripgrep not installed")
def test_kimi_grep_finds_content(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    (tmp_path / "sample.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    payload = json.loads(kimi_grep("hello", path=".", output_mode="content"))

    assert payload["matches"][0]["path"] == "sample.py"
    assert payload["matches"][0]["line"] == 1
    assert "def hello" in payload["matches"][0]["text"]


@pytest.mark.skipif(__import__("shutil").which("rg") is None, reason="ripgrep not installed")
def test_openai_grep_files_lists_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    (tmp_path / "sample.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    payload = json.loads(openai_grep_files("hello", path="."))

    assert payload["files"] == ["sample.py"]


def test_delete_file_is_registered():
    assert "delete_file" in TOOLS
    assert any(schema["name"] == "delete_file" for schema in TOOL_SCHEMAS)


def test_delete_file_removes_file(tmp_path, monkeypatch):
    from langbridge_cli.tools.filesystem import delete_file

    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    target = tmp_path / "stale.txt"
    target.write_text("remove me", encoding="utf-8")

    result = delete_file("stale.txt")

    assert result == "Deleted stale.txt."
    assert not target.exists()


def test_delete_file_rejects_directories(tmp_path, monkeypatch):
    from langbridge_cli.tools.filesystem import delete_file

    monkeypatch.setattr("langbridge_cli.tools.filesystem.WORKSPACE_ROOT", tmp_path)
    (tmp_path / "folder").mkdir()

    with pytest.raises(IsADirectoryError, match="Not a file"):
        delete_file("folder")
