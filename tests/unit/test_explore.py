import pytest

from langbridge_code.prompt.system.explorer import EXPLORER_PROMPT
from langbridge_code.agents.common.workspace import workspace_scope
from langbridge_code.agents.explorer import (
    EXPLORE_REPORT_MAX_CHARS,
    EXPLORE_REPORT_PREVIEW_CHARS,
    build_explore_prompt,
    collect_git_context,
    format_explore_output,
    read_only_bash,
    write_report_copy,
)
from langbridge_code.tools import execution


def test_explore_bash_write_guard_blocks_writes():
    assert execution.bash_write_guard("rm -rf foo", role="Explore agent") is not None
    assert execution.bash_write_guard("git log -1", role="Explore agent") is None


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
        "langbridge_code.agents.explorer.collect_git_context",
        lambda cwd=None: "<git-context>\nbranch: main\n</git-context>",
    )
    prompt = build_explore_prompt("find auth handlers", thoroughness="medium")
    assert "<git-context>" in prompt
    assert "find auth handlers" in prompt
    assert "Thoroughness: medium" in prompt


def test_collect_git_context_empty_when_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.agents.explorer.WORKSPACE_ROOT", tmp_path)
    assert collect_git_context() == ""


def test_format_explore_output_inlines_small_reports():
    output = format_explore_output("auth flow", "Found it in auth.py:12.")
    assert output == "[auth flow] Explore findings:\n\nFound it in auth.py:12."


def test_format_explore_output_points_at_persisted_report(tmp_path):
    report = "## Answer\n" + ("x" * (EXPLORE_REPORT_MAX_CHARS + 100))
    report_path = tmp_path / "explore-auth-flow" / "report-1.md"
    output = format_explore_output("big dig", report, report_path=report_path)

    assert "too large" in output
    assert str(report_path) in output
    assert report[:EXPLORE_REPORT_PREVIEW_CHARS] in output
    assert len(output) < EXPLORE_REPORT_MAX_CHARS


def test_format_explore_output_truncates_without_report_path():
    report = "x" * (EXPLORE_REPORT_MAX_CHARS + 100)
    output = format_explore_output("big dig", report, report_path=None)
    assert "truncated" in output
    assert len(output) < EXPLORE_REPORT_MAX_CHARS + 200


def test_write_report_copy_saves_next_to_trace(tmp_path):
    trace = tmp_path / "explore-auth-flow" / "explore-1.md"
    trace.parent.mkdir(parents=True)
    dest = write_report_copy(trace, 1, "## Answer\nfindings")
    assert dest == trace.parent / "report-1.md"
    assert dest.read_text(encoding="utf-8") == "## Answer\nfindings"

    # No trace dir (run without task_name) or empty report: no-op, no crash.
    assert write_report_copy(None, None, "## Answer") is None
    assert write_report_copy(trace, 1, "   ") is None


def test_format_explore_output_preview_cuts_at_newline(tmp_path):
    line = "finding line\n"
    report = line * ((EXPLORE_REPORT_MAX_CHARS // len(line)) + 10)
    output = format_explore_output("dig", report, report_path=tmp_path / "report-1.md")
    preview = output.split("chars):\n\n", 1)[1]
    assert len(preview) <= EXPLORE_REPORT_PREVIEW_CHARS
    assert preview.endswith("finding line")


def test_read_file_follows_registered_session_report(tmp_path):
    from langbridge_code.agents.common.workspace import add_readable_root
    from langbridge_code.tools.filesystem import read_file, write

    session_dir = tmp_path / "session"
    report = session_dir / "explore-auth-flow" / "report-1.md"
    report.parent.mkdir(parents=True)
    report.write_text("## Answer\nfindings\n", encoding="utf-8")

    workspace = tmp_path / "repo"
    workspace.mkdir()
    with workspace_scope(workspace):
        # Unregistered absolute paths outside the workspace stay blocked.
        with pytest.raises(ValueError):
            read_file(str(report))

        add_readable_root(session_dir)
        assert "findings" in read_file(str(report))

        # The carve-out is read-only: writes must still be refused.
        with pytest.raises(ValueError):
            write(str(report), "overwrite")
