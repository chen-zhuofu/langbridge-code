from langbridge_code.prompt.system import LANGBRIDGE_PROMPT, langbridge_system_prompt
from langbridge_code.prompt.system import WORKER_ENGINEER_PROMPT
from langbridge_code.prompt.system.reviewer import REVIEWER_ENGINEER_PROMPT


def test_main_agent_identity_prompt():
    assert "LangBridge Code" in LANGBRIDGE_PROMPT
    assert "Do not reveal" in LANGBRIDGE_PROMPT


def test_langbridge_system_prompt_covers_answer_and_delegate():
    from langbridge_code.agents.main_agent import MAIN_AGENT_TOOL_SCHEMAS

    prompt = langbridge_system_prompt()
    tool_names = {schema["name"] for schema in MAIN_AGENT_TOOL_SCHEMAS}
    assert "LangBridge Code" in prompt
    assert "tool schemas" in prompt
    assert {"agent_planner", "agent_explorer", "agent_worker"} <= tool_names
    assert "继续" in prompt or "continue" in prompt.lower()
    assert "worker-reviewer loop" in prompt.lower() or "one task" in prompt.lower()
    assert "Subagent-driven execution" in prompt
    assert "superpowers_writing-plans" not in prompt
    assert "answer in conversation" in prompt.lower() or "answer directly" in prompt.lower()
    # Tool how-to belongs in schemas; workflow mentions (todo_list.md file tools,
    # agent_worker) are OK.
    for tool_name in ("ask_user", "grep"):
        assert tool_name not in prompt


def test_main_agent_records_every_subagent_result_in_progress():
    prompt = langbridge_system_prompt()
    assert "Every time any subagent returns a result" in prompt
    assert "call note_progress exactly once for that returned result" in prompt
    assert "including failures and partial results" in prompt
    assert "one note_progress call per result" in prompt


def test_main_agent_must_resolve_consequential_ambiguity_before_acting():
    prompt = langbridge_system_prompt()

    assert "Ambiguity gate" in prompt
    assert "two\nor more interpretations" in prompt
    assert "MUST ask the user to choose before continuing" in prompt
    assert "answer is still compatible with multiple" in prompt
    assert '"Mac app"' in prompt
    assert "substantial rework" in prompt


def test_engineering_guidelines_live_in_specialist_prompts():
    assert "Think before coding." not in WORKER_ENGINEER_PROMPT
    assert "WORKER_STATUS: READY_FOR_REVIEW" in WORKER_ENGINEER_PROMPT
    assert "REVIEW_VERDICT: PASS" in REVIEWER_ENGINEER_PROMPT
