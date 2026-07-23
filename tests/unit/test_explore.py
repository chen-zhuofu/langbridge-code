import pytest

from langbridge_code.agents.system_prompt.explorer import EXPLORER_PROMPT
from langbridge_code.tools.agent_explorer import (
    build_explore_prompt,
    collect_git_context,
    explore_bash_guard,
    read_only_bash,
)


def test_explore_bash_guard_blocks_writes():
    assert explore_bash_guard({"command": "rm -rf foo"}) is not None
    assert explore_bash_guard({"command": "git log -1"}) is None


def test_read_only_bash_rejects_write_commands(monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.tools.execution.TOOLS",
        {"bash": lambda **kwargs: "ran"},
    )
    with pytest.raises(PermissionError):
        read_only_bash(command="rm -rf /tmp/x")


def test_explorer_prompt_requires_structured_report_sections():
    assert "## Searches run" in EXPLORER_PROMPT
    assert "## Current state" in EXPLORER_PROMPT
    assert "## Open questions" in EXPLORER_PROMPT
    assert "path:line" in EXPLORER_PROMPT
    assert "READ-ONLY MODE" in EXPLORER_PROMPT
    assert "read_webpage" in EXPLORER_PROMPT
    assert "read-only" in EXPLORER_PROMPT.lower()


def test_build_explore_prompt_includes_git_context(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "langbridge_code.tools.agent_explorer.collect_git_context",
        lambda cwd=None: "<git-context>\nbranch: main\n</git-context>",
    )
    prompt = build_explore_prompt("find auth handlers", thoroughness="medium")
    assert "<git-context>" in prompt
    assert "find auth handlers" in prompt
    assert "Thoroughness: medium" in prompt


def test_collect_git_context_empty_when_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.tools.agent_explorer.WORKSPACE_ROOT", tmp_path)
    assert collect_git_context() == ""
