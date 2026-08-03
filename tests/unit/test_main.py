import os
from datetime import date

from langbridge_code.prompt.system import LANGBRIDGE_PROMPT, langbridge_system_prompt
from langbridge_code.prompt.system import WORKER_ENGINEER_PROMPT
from langbridge_code.prompt.system import REVIEWER_ENGINEER_PROMPT


def _normalized(text):
    """Collapse whitespace so assertions survive line-wrap changes."""
    return " ".join(text.split())


def test_main_agent_identity_prompt():
    assert "LangBridge Code" in LANGBRIDGE_PROMPT
    assert "Do not reveal" in LANGBRIDGE_PROMPT


def test_langbridge_system_prompt_covers_answer_and_delegate():
    from langbridge_code.agents.main_agent import MAIN_AGENT_TOOL_SCHEMAS

    prompt = langbridge_system_prompt()
    tool_names = {schema["name"] for schema in MAIN_AGENT_TOOL_SCHEMAS}
    assert "LangBridge Code" in prompt
    assert {"agent_planner", "agent_explorer", "agent_worker"} <= tool_names
    assert "继续" in prompt or "continue" in prompt.lower()
    assert "worker-reviewer loop" in prompt.lower()
    assert "# Subagents and orchestration" in prompt
    assert "# User interaction" in prompt
    assert "superpowers_writing-plans" not in prompt
    assert "answer in conversation" in prompt.lower()


def test_langbridge_prompt_has_seven_content_areas():
    # Anchor headers for the agreed section coverage (workflow, subagents +
    # orchestration, context, memory, skills, user interaction, safety, env).
    prompt = langbridge_system_prompt()
    for header in (
        "# Workflow",
        "# Subagents and orchestration",
        "# Context management",
        "# Memory",
        "# Skills",
        "# User interaction",
        "# Safety and destructive actions",
        "# Environment",
    ):
        assert header in prompt, header


def test_main_agent_records_every_subagent_result_in_progress():
    normalized = _normalized(langbridge_system_prompt())
    assert "note_progress" in normalized
    assert "once after every subagent return" in normalized
    assert "including failures and partial results" in normalized
    assert "one call per result" in normalized


def test_main_agent_must_resolve_consequential_ambiguity_before_acting():
    prompt = langbridge_system_prompt()
    normalized = _normalized(prompt)

    assert "Ambiguity gate" in prompt
    assert "MUST ask the user to choose before continuing" in normalized
    assert "substantial rework" in normalized


def test_environment_block_reports_runtime_facts():
    prompt = langbridge_system_prompt()
    assert "# Environment" in prompt
    assert os.getcwd() in prompt
    assert date.today().isoformat() in prompt
    assert "Is a git repository" in prompt


def test_safety_section_covers_destructive_actions():
    normalized = _normalized(LANGBRIDGE_PROMPT)
    assert "# Safety and destructive actions" in LANGBRIDGE_PROMPT
    assert "git reset --hard" in normalized
    assert "reversibility and blast radius" in normalized


def test_skills_section_requires_reading_before_acting():
    normalized = _normalized(LANGBRIDGE_PROMPT)
    assert "# Skills" in LANGBRIDGE_PROMPT
    assert "read_skill" in normalized
    assert "never act on the task before reading it" in normalized


def test_engineering_guidelines_live_in_specialist_prompts():
    assert "Think before coding." not in WORKER_ENGINEER_PROMPT
    assert "WORKER_STATUS: READY_FOR_REVIEW" in WORKER_ENGINEER_PROMPT
    assert "REVIEW_VERDICT: PASS" in REVIEWER_ENGINEER_PROMPT
