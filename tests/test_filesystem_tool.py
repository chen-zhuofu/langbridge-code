import pytest

from langbridge_cli.tools import TOOL_SCHEMAS, TOOLS
from langbridge_cli.tools.filesystem import delete_file


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
