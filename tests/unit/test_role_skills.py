from langbridge_code.prompt.system import WORKER_ENGINEER_PROMPT, worker_system_prompt
from langbridge_code.prompt.system.planner import PLANNER_PROMPT, planner_system_prompt
from langbridge_code.prompt.system.explorer import explorer_system_prompt
from langbridge_code.prompt.system.langbridge import langbridge_system_prompt
from langbridge_code.skills import (
    attach_skill_tracking,
    ensure_skill_index_block,
    explorer_skill_catalog,
    format_invoked_skills_block,
    langbridge_skill_catalog,
    list_skills,
    planner_skill_catalog,
    record_invoked_skill,
    worker_skill_catalog,
    reviewer_skill_catalog,
)
from langbridge_code.agents.explorer import EXPLORE_TOOL_NAMES
from langbridge_code.agents.planner import PLANNER_TOOL_NAMES
from langbridge_code.context.common.stack import ContextStack, INVOKED_SKILLS_TAG, SKILL_INDEX_TAG


def test_planner_skill_catalog_reads_planner_folder():
    # Planner folder is empty on purpose; catalog comes from disk, not a name list.
    assert planner_skill_catalog() == ""
    assert {name for name, _ in list_skills("planner")} == set()


def test_worker_skill_catalog_includes_coder_expertise():
    catalog = worker_skill_catalog("coding")
    assert "superpowers_test-driven-development" in catalog
    assert "superpowers_systematic-debugging" in catalog
    assert "reviewer_code" not in catalog
    # Only skills present under skills/worker_coder/ appear.
    assert "presentation-skill" not in catalog


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
    assert "writing-simple-plans" in catalog
    assert "superpowers_systematic-debugging" in catalog
    # Folder contents are the source of truth (includes draft brainstorming).
    assert "langbridge_brainstorming" in catalog
    assert "superpowers_test-driven-development" not in catalog
    assert "clean-code-guard" not in catalog


def test_ensure_skill_index_pins_full_catalog_without_llm():
    catalog = worker_skill_catalog("coding")
    stack = ContextStack(system_content="sys")
    ensure_skill_index_block(stack, "key", "model", "fix a bug", catalog)
    assert stack.skill_index_block == catalog
    # Idempotent: second call does not change.
    ensure_skill_index_block(stack, "key", "model", "other task", "should-not-replace")
    assert stack.skill_index_block == catalog


def test_compaction_drops_listing_and_repins_invoked_skills():
    catalog = worker_skill_catalog("coding")
    stack = ContextStack(system_content="sys", raw_keep=1, compact_threshold_tokens=1)
    ensure_skill_index_block(stack, None, None, "task", catalog)
    record_invoked_skill(
        stack,
        "superpowers_test-driven-development",
        body="# TDD\nWrite a failing test first.",
    )
    record_invoked_skill(
        stack,
        "superpowers_systematic-debugging",
        body="# Debug\nReproduce before fixing.",
    )
    assert stack.skill_index_block
    assert not stack.invoked_skills_block

    # Force compaction by overflowing the tiny threshold.
    stack.start_turn("u1")
    stack.complete_step([{"role": "assistant", "content": "a1"}])
    stack.start_turn("u2")
    stack.complete_step([{"role": "assistant", "content": "a2"}])
    stats = stack.maybe_advance(budget_tokens=1)
    assert stats["compacted"] is True
    assert stack.skill_index_block is None
    assert stack.skills_listing_cleared is True
    assert stack.invoked_skills_block
    assert "superpowers_systematic-debugging" in stack.invoked_skills_block
    assert "Reproduce before fixing" in stack.invoked_skills_block
    # Most recent invoked skill appears first in the re-pinned block.
    assert stack.invoked_skills_block.index(
        "superpowers_systematic-debugging"
    ) < stack.invoked_skills_block.index("superpowers_test-driven-development")
    # Listing must not come back after compaction.
    ensure_skill_index_block(stack, None, None, "task", catalog)
    assert stack.skill_index_block is None
    messages = stack.to_messages()
    assert not any(
        str(m.get("content", "")).startswith(f"<{SKILL_INDEX_TAG}>") for m in messages
    )
    assert any(
        str(m.get("content", "")).startswith(f"<{INVOKED_SKILLS_TAG}>") for m in messages
    )


def test_attach_skill_tracking_records_successful_reads():
    stack = ContextStack(system_content="sys")
    tools = {"read_skill": lambda name: f"body of {name}"}
    attach_skill_tracking(stack, tools, role="worker_coder")
    assert tools["read_skill"]("demo") == "body of demo"
    assert stack.invoked_skills == [{"name": "demo", "body": "body of demo"}]
    tools["read_skill"]("demo/references/x.md")
    assert [e["name"] for e in stack.invoked_skills] == ["demo"]
    assert stack.invoked_skills[0]["body"] == "body of demo/references/x.md"
    tools2 = {"read_skill": lambda name: f"Tool error: unknown skill '{name}'"}
    attach_skill_tracking(stack, tools2)
    before = list(stack.invoked_skills)
    tools2["read_skill"]("missing")
    assert stack.invoked_skills == before


def test_format_invoked_skills_respects_total_budget():
    invoked = [
        {"name": "a", "body": "A" * 400},
        {"name": "b", "body": "B" * 400},
        {"name": "c", "body": "C" * 400},
    ]
    block = format_invoked_skills_block(invoked, per_skill_tokens=50, total_tokens=80)
    assert "## c" in block


def test_worker_session_sets_skill_index_block():
    from langbridge_code.agents.worker_reviewer import WorkerSession

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
    from langbridge_code.prompt.system.reviewer import reviewer_system_prompt

    prompt = reviewer_system_prompt("coding")
    assert "worker-reviewer loop" in prompt.lower()
    assert "REVIEW_VERDICT: PASS" in prompt


def test_planner_has_read_skill_tool():
    assert "read_skill" in PLANNER_TOOL_NAMES
    assert "bash" in PLANNER_TOOL_NAMES
    assert "read_webpage" in PLANNER_TOOL_NAMES


def test_explorer_has_read_skill_tool():
    assert "read_skill" in EXPLORE_TOOL_NAMES


def test_worker_coding_catalog_is_expertise_only():
    catalog = worker_skill_catalog("coding")
    assert "superpowers_test-driven-development" in catalog
    assert "karpathy_simplicity-first" not in catalog


def test_explorer_skill_catalog_is_empty():
    assert explorer_skill_catalog() == ""
    assert {name for name, _ in list_skills("explorer")} == set()


def test_explorer_prompt_stays_a_narrow_searcher():
    prompt = explorer_system_prompt()
    assert "Systematic debugging" not in prompt
    assert "NO FIXES WITHOUT ROOT CAUSE" not in prompt
    assert "READ-ONLY" in prompt
    assert "do NOT implement or propose" in prompt
    assert "Role playbooks" not in prompt
    assert "superpowers_subagent-driven-development" not in prompt
    # Explorer shares the main agent's skill mechanism: <skill_index> + read_skill.
    assert "<skill_index>" in prompt
    assert "<progress>" in prompt
    assert "<invoked_skills>" in prompt
