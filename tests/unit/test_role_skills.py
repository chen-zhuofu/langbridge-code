from langbridge_code.agents.system_prompt import WORKER_ENGINEER_PROMPT, worker_system_prompt
from langbridge_code.agents.system_prompt.planner import PLANNER_PROMPT, planner_system_prompt
from langbridge_code.agents.system_prompt.explorer import explorer_system_prompt
from langbridge_code.agents.system_prompt.langbridge import langbridge_system_prompt
from langbridge_code.skills import (
    EXPLORER_SKILL_NAMES,
    PLANNER_SKILL_NAMES,
    WORKER_CODING_SKILL_NAMES,
    skill_catalog_text_for,
    worker_skill_catalog,
    reviewer_skill_catalog,
)
from langbridge_code.tools.agent_explorer import EXPLORE_TOOL_NAMES
from langbridge_code.tools.agent_planner import PLANNER_TOOL_NAMES


def test_planner_skill_catalog_excludes_coder_only_skills():
    catalog = skill_catalog_text_for(PLANNER_SKILL_NAMES)
    assert "superpowers_writing-plans" in catalog
    assert "superpowers_brainstorming" in catalog
    assert "superpowers_test-driven-development" not in catalog


def test_worker_skill_catalog_includes_coder_expertise():
    catalog = worker_skill_catalog("coding")
    assert "superpowers_test-driven-development" in catalog
    assert "karpathy_think-before-coding" in catalog
    assert "reviewer_code" not in catalog


def test_worker_slide_catalog_has_no_expertise_skills():
    catalog = worker_skill_catalog("slide")
    assert catalog == ""


def test_reviewer_slide_catalog_has_no_expertise_skills():
    catalog = reviewer_skill_catalog("slide")
    assert catalog == ""


def test_reviewer_coding_catalog_has_no_expertise_skills():
    catalog = reviewer_skill_catalog("coding")
    assert catalog == ""


def test_planner_prompt_owns_planning():
    assert "planner" in PLANNER_PROMPT.lower()
    assert "writing-plans" in PLANNER_PROMPT.lower() or "Role playbooks" in planner_system_prompt()
    assert "todo list" in PLANNER_PROMPT.lower()
    assert "<!-- integration -->" in PLANNER_PROMPT
    assert "Out of scope" in PLANNER_PROMPT
    assert "Key discoveries" in PLANNER_PROMPT
    assert "Changes required" in PLANNER_PROMPT
    assert "verify:" in PLANNER_PROMPT
    assert "ask_user" not in PLANNER_PROMPT.lower()
    assert "read_file" not in PLANNER_PROMPT.lower()


def test_worker_prompt_does_not_own_planning():
    assert "planner" in WORKER_ENGINEER_PROMPT.lower()
    assert "do not call update_plan" in WORKER_ENGINEER_PROMPT.lower()
    assert "edit the todo_list" in WORKER_ENGINEER_PROMPT.lower()
    assert "read_plan" in WORKER_ENGINEER_PROMPT.lower()


def test_worker_coding_prompt_includes_general_loop_guidance():
    prompt = worker_system_prompt("coding")
    assert "worker-reviewer loop" in prompt.lower()
    assert "READY_FOR_REVIEW" in prompt
    assert "superpowers_test-driven-development" in prompt


def test_reviewer_coding_prompt_includes_general_loop_guidance():
    from langbridge_code.agents.system_prompt.reviewer import reviewer_system_prompt

    prompt = reviewer_system_prompt("coding")
    assert "worker-reviewer loop" in prompt.lower()
    assert "REVIEW_VERDICT: PASS" in prompt


def test_planner_has_read_skill_tool():
    assert "read_skill" in PLANNER_TOOL_NAMES


def test_explorer_has_read_skill_tool():
    assert "read_skill" in EXPLORE_TOOL_NAMES


def test_role_prompts_inject_scoped_expertise_only():
    planner = planner_system_prompt()
    worker = worker_system_prompt("coding")
    langbridge = langbridge_system_prompt()
    assert "superpowers_writing-plans" in planner
    assert "superpowers_writing-plans" not in worker
    assert "superpowers_test-driven-development" in worker
    assert "Subagent-driven execution" in langbridge
    assert "superpowers_test-driven-development" not in langbridge


def test_worker_coding_skill_names_are_expertise_only():
    catalog = skill_catalog_text_for(WORKER_CODING_SKILL_NAMES)
    assert "superpowers_test-driven-development" in catalog
    assert "karpathy_simplicity-first" not in catalog


def test_explorer_skill_catalog_is_expertise_only():
    catalog = skill_catalog_text_for(EXPLORER_SKILL_NAMES)
    assert "superpowers_systematic-debugging" in catalog
    assert "karpathy_goal-driven" not in catalog


def test_explorer_prompt_injects_scoped_skills():
    prompt = explorer_system_prompt()
    assert "superpowers_systematic-debugging" in prompt
    assert "Role playbooks" in prompt
    assert "read_skill" not in prompt
    assert "superpowers_subagent-driven-development" not in prompt
