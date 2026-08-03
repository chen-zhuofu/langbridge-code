import json
from pathlib import Path

import pytest

from langbridge_code.agents.common.workspace import (
    configure_agent_artifacts,
    set_workspace_root,
)
from langbridge_code.tools import TOOL_SCHEMAS, TOOLS
from langbridge_code.tools.execution import (
    EXECUTION_OUTPUT_PREVIEW_CHARS,
    bash,
    prepare_execution_output,
)
from langbridge_code.tools.filesystem import read_file
from langbridge_code.util.trace_log import TraceContext, set_trace_context


@pytest.fixture
def isolated_workspace(tmp_path):
    set_workspace_root(tmp_path)
    yield tmp_path
    set_workspace_root(None)
    set_trace_context(None)
    configure_agent_artifacts(None, label="LangBridge")


def test_bash_is_registered():
    assert "bash" in TOOLS
    assert any(schema["name"] == "bash" for schema in TOOL_SCHEMAS)


def test_bash_runs_shell_command(isolated_workspace):
    result = json.loads(bash("echo 'hello from tool'"))

    assert result["command"] == "echo 'hello from tool'"
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert result["truncated"] is False
    assert result["output"] == "hello from tool\n"
    assert "output_path" not in result


def test_bash_rejects_cwd_outside_workspace(isolated_workspace):
    with pytest.raises(ValueError, match="Path must stay inside the current workspace"):
        bash("pwd", cwd="..")


def test_bash_rejects_empty_command():
    with pytest.raises(ValueError, match="command must be a non-empty string"):
        bash("   ")


def test_bash_rejects_privileged_commands(isolated_workspace):
    with pytest.raises(ValueError, match="Privileged commands"):
        bash("sudo apt install chromium")
    with pytest.raises(ValueError, match="Privileged commands"):
        bash("echo ok && sudo rm -rf /")


def test_prepare_execution_output_inline_under_limit():
    text = "x" * 100
    inline, truncated, path = prepare_execution_output(text)
    assert truncated is False
    assert path is None
    assert inline == text


def test_prepare_execution_output_spills_to_main_attachments(
    isolated_workspace, monkeypatch
):
    monkeypatch.setattr(
        "langbridge_code.tools.execution.MAX_EXECUTION_OUTPUT_CHARS", 100
    )
    session = isolated_workspace / "session-test"
    session.mkdir()
    configure_agent_artifacts(session, label="LangBridge")

    full = ("HEAD-" + ("body" * 80) + "-TAIL\n") * 40
    assert len(full) > 100
    assert len(full) > EXECUTION_OUTPUT_PREVIEW_CHARS
    inline, truncated, path = prepare_execution_output(full, run_log_path=session)

    assert truncated is True
    assert path is not None
    assert path.is_file()
    assert path.parent == (session / "main" / "attachments").resolve()
    assert path.read_text(encoding="utf-8") == full
    assert inline.startswith(full[:EXECUTION_OUTPUT_PREVIEW_CHARS])
    assert str(path) in inline
    assert "read_file" in inline
    # Context keeps only the head preview + notice, not the full body.
    assert len(inline) < len(full)


def test_bash_oversized_output_spills_and_is_readable(isolated_workspace, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.tools.execution.MAX_EXECUTION_OUTPUT_CHARS", 200
    )
    session = isolated_workspace / "session-bash"
    session.mkdir()
    configure_agent_artifacts(session, label="LangBridge")
    set_trace_context(TraceContext(run_log_path=session, trace_id="t1"))

    # 300 'a' chars via python — over the 200-char spill threshold.
    result = json.loads(
        bash("python3 -c \"print('a' * 300)\"", run_log_path=session)
    )
    assert result["truncated"] is True
    assert result["exit_code"] == 0
    path = Path(result["output_path"])
    assert path.is_file()
    assert path.read_text(encoding="utf-8").startswith("a" * 200)
    assert "output truncated" in result["output"]
    assert str(path) in result["output"]

    # Absolute path under the main agent's attachments must be readable.
    listed = read_file(str(path), offset=1, limit=5)
    assert "aaa" in listed


def test_prepare_execution_output_requires_session(isolated_workspace, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.tools.execution.MAX_EXECUTION_OUTPUT_CHARS", 50
    )
    configure_agent_artifacts(None, label="LangBridge")
    with pytest.raises(RuntimeError, match="No session directory"):
        prepare_execution_output("z" * 120)
