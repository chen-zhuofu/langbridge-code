import pytest

from langbridge_code.skills import list_skills, load_skill
from langbridge_code.tools import skills as skills_tools
from langbridge_code.agents.planner import PLANNER_TOOLS
from langbridge_code.tools import MAIN_TOOLS


def test_agent_skills_are_discoverable():
    names = {name for name, _ in list_skills()}
    assert "superpowers_test-driven-development" in names
    assert "superpowers_systematic-debugging" in names
    # Karpathy guidance is inlined in the worker system prompt, not a skill.
    assert "karpathy_think-before-coding" not in names
    assert "karpathy_surgical-changes" not in names


def test_superpowers_skill_has_body():
    body = load_skill("superpowers_test-driven-development")
    assert "test" in body.lower()
    assert len(body) > 100


def test_list_skills_for_role():
    planner_names = {name for name, _ in list_skills("planner")}
    assert "superpowers_brainstorming" in planner_names
    assert "superpowers_test-driven-development" not in planner_names

    langbridge_names = {name for name, _ in list_skills("langbridge")}
    assert "grilling" in langbridge_names
    assert "writing-simple-plans" in langbridge_names

    explorer_names = {name for name, _ in list_skills("explorer")}
    assert explorer_names == set()

    worker_names = {name for name, _ in list_skills("worker_coder")}
    assert "superpowers_test-driven-development" in worker_names
    assert "superpowers_using-git-worktrees" not in worker_names

    reviewer_names = {name for name, _ in list_skills("reviewer_code")}
    assert "clean-code-guard" in reviewer_names
    assert "test-guard" in reviewer_names
    assert "docs-guard" in reviewer_names


def test_load_skill_respects_role_scope():
    body = load_skill("grilling", role="langbridge")
    assert "grill" in body.lower()

    with pytest.raises(FileNotFoundError):
        load_skill("grilling", role="planner")

    with pytest.raises(FileNotFoundError):
        load_skill("superpowers_test-driven-development", role="langbridge")

    assert "test" in load_skill(
        "superpowers_test-driven-development", role="worker_coder"
    ).lower()


def test_agent_read_skill_tools_are_role_scoped():
    # Main agent can load langbridge skills, not planner/worker ones.
    assert "grill" in MAIN_TOOLS["read_skill"]("grilling").lower()
    assert "unknown skill" in MAIN_TOOLS["read_skill"](
        "superpowers_writing-plans"
    ).lower()

    # Planner cannot load main-agent grilling.
    assert "unknown skill" in PLANNER_TOOLS["read_skill"]("grilling").lower()
    assert "plan" in PLANNER_TOOLS["read_skill"](
        "superpowers_writing-plans"
    ).lower()

    schema = skills_tools.read_skill_schema("planner")
    assert "superpowers_writing-plans" in schema["description"]
    assert "grilling" not in schema["description"]


def test_guard_skill_reference_loads():
    body = load_skill("clean-code-guard")
    assert "LangBridge Code mapping (reviewer)" in body
    assert "Review mode" in body

    ref = load_skill("clean-code-guard/references/ai-failure-modes.md")
    assert "failure" in ref.lower()
    assert len(ref) > 100
