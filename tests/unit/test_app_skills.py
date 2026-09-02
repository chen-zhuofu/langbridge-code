import pytest

from langbridge_code.agents.common.workspace import workspace_scope
from langbridge_code.skills import list_skills, load_skill, resolve_skill_dir
from langbridge_code.tools import (
    GOAL_VERIFICATION_TOOL_NAMES,
    MAIN_TOOL_NAMES,
    MAIN_TOOLS,
    TOOLS,
)


def _skill_document(name="verify-widget-build"):
    return (
        "---\n"
        f"name: {name}\n"
        "description: Verify widget builds after compiler or packaging changes.\n"
        "---\n\n"
        "# Workflow\n\n1. Run the focused tests.\n2. Build the package.\n"
    )


def test_main_file_tools_create_and_edit_app_skill(tmp_path, monkeypatch):
    support = tmp_path / "Application Support" / "LangBridge"
    monkeypatch.setenv("LANGBRIDGE_APP_SUPPORT_DIR", str(support))
    destination = support / "skills/langbridge/verify-widget-build/SKILL.md"

    result = MAIN_TOOLS["write"](
        path=str(destination),
        content=_skill_document(),
    )

    assert result.startswith("Wrote")
    assert "# Workflow" in load_skill("verify-widget-build", role="langbridge")
    assert (
        "verify-widget-build",
        "Verify widget builds after compiler or packaging changes.",
    ) in list_skills(role="langbridge")
    assert resolve_skill_dir("verify-widget-build", role="langbridge") == destination.parent

    MAIN_TOOLS["Edit"](
        path=str(destination),
        old_string="Run the focused tests.",
        new_string="Run all focused tests.",
    )
    assert "Run all focused tests." in load_skill(
        "verify-widget-build", role="langbridge"
    )


def test_non_main_file_tools_cannot_write_app_skills(tmp_path, monkeypatch):
    support = tmp_path / "Application Support" / "LangBridge"
    monkeypatch.setenv("LANGBRIDGE_APP_SUPPORT_DIR", str(support))
    destination = support / "skills/langbridge/not-allowed/SKILL.md"

    with pytest.raises(ValueError, match="Path must stay"):
        TOOLS["write"](str(destination), _skill_document("not-allowed"))


def test_project_local_skills_are_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_APP_SUPPORT_DIR", str(tmp_path / "app-support"))
    project_skill = tmp_path / ".langbridge/skills/project-only"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text(
        _skill_document("project-only"), encoding="utf-8"
    )

    with workspace_scope(tmp_path):
        assert "project-only" not in dict(list_skills(role="langbridge"))
        with pytest.raises(FileNotFoundError):
            load_skill("project-only", role="langbridge")


def test_skill_writing_is_a_meta_skill_not_a_tool():
    assert "writing-app-skills" in dict(list_skills(role="langbridge"))
    assert "write_skill" not in MAIN_TOOL_NAMES
    assert "browser" in MAIN_TOOL_NAMES
    assert "browser" not in GOAL_VERIFICATION_TOOL_NAMES
