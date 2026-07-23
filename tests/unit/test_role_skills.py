from langbridge_code.agents.system_prompt import WORKER_ENGINEER_PROMPT, worker_system_prompt
from langbridge_code.agents.system_prompt.planner import PLANNER_PROMPT, planner_system_prompt
from langbridge_code.agents.system_prompt.explorer import explorer_system_prompt
from langbridge_code.agents.system_prompt.langbridge import langbridge_system_prompt
from langbridge_code.skills import (
    EXPLORER_SKILL_NAMES,
    PLANNER_SKILL_NAMES,
    WORKER_CODING_SKILL_NAMES,
    langbridge_skill_catalog,
    select_skill_index,
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
    assert "superpowers_systematic-debugging" in catalog
    assert "reviewer_code" not in catalog


def test_legacy_slide_task_type_coerces_to_coding_catalog():
    assert worker_skill_catalog("slide") == worker_skill_catalog("coding")
    assert reviewer_skill_catalog("slide") == reviewer_skill_catalog("coding")


def test_reviewer_coding_catalog_has_guard_skills():
    catalog = reviewer_skill_catalog("coding")
    assert "clean-code-guard" in catalog
    assert "test-guard" in catalog
    assert "docs-guard" in catalog
    assert "superpowers_test-driven-development" not in catalog


def test_role_system_prompts_do_not_inline_playbooks():
    # Skills are injected per task as a <skill_index> context block.
    for prompt in (
        langbridge_system_prompt(),
        worker_system_prompt("coding"),
        planner_system_prompt(),
    ):
        assert "Role playbooks" not in prompt
        assert "superpowers_writing-plans" not in prompt
        assert "superpowers_test-driven-development" not in prompt


def test_langbridge_catalog_scoped_to_main_agent_skills():
    catalog = langbridge_skill_catalog()
    assert "grilling" in catalog
    assert "superpowers_test-driven-development" not in catalog
    assert "clean-code-guard" not in catalog


def test_select_skill_index_falls_back_to_full_catalog_without_api():
    catalog = worker_skill_catalog("coding")
    assert select_skill_index(None, None, "fix a bug", catalog) == catalog
    assert select_skill_index("key", "model", "task", "") == ""


def test_select_skill_index_filters_catalog_lines(monkeypatch):
    catalog = worker_skill_catalog("coding")

    def fake_response(api_key, model, messages, **kwargs):
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "superpowers_test-driven-development\nsuperpowers_systematic-debugging",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr("langbridge_code.llm.client.create_model_response", fake_response)
    selected = select_skill_index("key", "model", "fix a bug", catalog)
    lines = selected.splitlines()
    assert len(lines) == 2
    assert any("superpowers_test-driven-development" in line for line in lines)
    assert any("superpowers_systematic-debugging" in line for line in lines)


def test_select_skill_index_none_reply_returns_empty(monkeypatch):
    def fake_response(api_key, model, messages, **kwargs):
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "NONE"}]}
            ]
        }

    monkeypatch.setattr("langbridge_code.llm.client.create_model_response", fake_response)
    assert select_skill_index("key", "model", "small talk", worker_skill_catalog("coding")) == ""


def test_select_skill_index_swallows_llm_failure(monkeypatch):
    catalog = worker_skill_catalog("coding")

    def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("langbridge_code.llm.client.create_model_response", boom)
    assert select_skill_index("key", "model", "task", catalog) == catalog


def test_worker_session_sets_skill_index_block():
    from langbridge_code.tools.agent_worker_reviewer import WorkerSession

    session = WorkerSession("key", "model", [], {}, task_type="coding")
    session.begin_send("do it", assigned_task="Build the parser")
    block = session.context.stack.skill_index_block
    assert block and "superpowers_test-driven-development" in block
    # Assembled messages carry the wrapped block before the live prompt.
    contents = [str(m.get("content", "")) for m in session.messages]
    assert any(c.startswith("<skill_index>") for c in contents)


def test_planner_prompt_owns_planning():
    assert "planner" in PLANNER_PROMPT.lower()
    assert "todo list" in PLANNER_PROMPT.lower()
    assert "<!-- integration -->" not in PLANNER_PROMPT
    assert "<!-- depends:" not in PLANNER_PROMPT
    assert "Out of scope" in PLANNER_PROMPT
    assert "Key discoveries" in PLANNER_PROMPT
    assert "Changes required" in PLANNER_PROMPT
    assert "verify:" in PLANNER_PROMPT.lower()
    assert "ask_user" not in PLANNER_PROMPT.lower()
    assert "do not ask the user" in PLANNER_PROMPT.lower()
    assert "ask_user" not in PLANNER_TOOL_NAMES
    assert "write" not in PLANNER_TOOL_NAMES


def test_worker_prompt_does_not_own_planning():
    assert "todo_list.md" in WORKER_ENGINEER_PROMPT
    assert "do not read or edit todo_list.md" in WORKER_ENGINEER_PROMPT.lower()
    assert "read_plan" not in WORKER_ENGINEER_PROMPT.lower()


def test_worker_coding_prompt_includes_general_loop_guidance():
    prompt = worker_system_prompt("coding")
    assert "worker-reviewer loop" in prompt.lower()
    assert "READY_FOR_REVIEW" in prompt


def test_worker_coding_prompt_tells_worker_to_commit_as_it_goes():
    prompt = worker_system_prompt("coding")
    assert "git commit" in prompt
    assert "never push" in prompt.lower()
    # Legacy slide task_type still gets the coding prompt.
    assert "git commit" in worker_system_prompt("slide")


def test_reviewer_coding_prompt_includes_general_loop_guidance():
    from langbridge_code.agents.system_prompt.reviewer import reviewer_system_prompt

    prompt = reviewer_system_prompt("coding")
    assert "worker-reviewer loop" in prompt.lower()
    assert "REVIEW_VERDICT: PASS" in prompt


def test_planner_has_read_skill_tool():
    assert "read_skill" in PLANNER_TOOL_NAMES
    assert "bash" in PLANNER_TOOL_NAMES
    assert "read_webpage" in PLANNER_TOOL_NAMES


def test_explorer_has_read_skill_tool():
    assert "read_skill" in EXPLORE_TOOL_NAMES


def test_worker_coding_skill_names_are_expertise_only():
    catalog = skill_catalog_text_for(WORKER_CODING_SKILL_NAMES)
    assert "superpowers_test-driven-development" in catalog
    assert "karpathy_simplicity-first" not in catalog


def test_explorer_skill_catalog_is_empty():
    assert EXPLORER_SKILL_NAMES == ()
    assert skill_catalog_text_for(EXPLORER_SKILL_NAMES) == ""


def test_explorer_prompt_inlines_debugging_guidance():
    prompt = explorer_system_prompt()
    assert "Systematic debugging" in prompt
    assert "NO FIXES WITHOUT ROOT CAUSE" in prompt
    assert "Role playbooks" not in prompt
    assert "superpowers_subagent-driven-development" not in prompt
    # Explorer shares the main agent's skill mechanism: <skill_index> + read_skill.
    assert "<skill_index>" in prompt
    assert "<progress>" in prompt
