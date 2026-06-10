import json
import sys

import pytest

from langbridge_cli.tools import TOOL_SCHEMAS, TOOLS
from langbridge_cli.tools.execution import execute_program


def test_execute_program_is_registered():
    assert "execute_program" in TOOLS
    assert any(schema["name"] == "execute_program" for schema in TOOL_SCHEMAS)


def test_execute_program_runs_non_interactive_command():
    result = json.loads(
        execute_program(
            sys.executable,
            ["-c", "print('hello from tool')"],
        )
    )

    assert result["command"] == [sys.executable, "-c", "print('hello from tool')"]
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert result["truncated"] is False
    assert result["output"] == "hello from tool\n"


def test_execute_program_rejects_cwd_outside_workspace():
    with pytest.raises(ValueError, match="Path must stay inside the current workspace"):
        execute_program(sys.executable, ["--version"], cwd="..")


def test_execute_program_requires_args_list():
    with pytest.raises(ValueError, match="args must be a list"):
        execute_program(sys.executable, "--version")
