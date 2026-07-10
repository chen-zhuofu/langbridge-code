import json

import pytest

from langbridge_code.tools import TOOL_SCHEMAS, TOOLS
from langbridge_code.tools.execution import bash


def test_bash_is_registered():
    assert "bash" in TOOLS
    assert any(schema["name"] == "bash" for schema in TOOL_SCHEMAS)


def test_bash_runs_shell_command():
    result = json.loads(bash("echo 'hello from tool'"))

    assert result["command"] == "echo 'hello from tool'"
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert result["truncated"] is False
    assert result["output"] == "hello from tool\n"


def test_bash_rejects_cwd_outside_workspace():
    with pytest.raises(ValueError, match="Path must stay inside the current workspace"):
        bash("pwd", cwd="..")


def test_bash_rejects_empty_command():
    with pytest.raises(ValueError, match="command must be a non-empty string"):
        bash("   ")


def test_bash_rejects_privileged_commands():
    with pytest.raises(ValueError, match="Privileged commands"):
        bash("sudo apt install chromium")
    with pytest.raises(ValueError, match="Privileged commands"):
        bash("echo ok && sudo rm -rf /")
