import pytest

from langbridge_code.context.common.stack import (
    ASSIGNED_TASK_PREFIX,
    ContextStack,
)


def _tool_step(call_id: str, name: str, output: str) -> list[dict]:
    return [
        {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": call_id, "output": output},
    ]


@pytest.fixture
def stack():
    return ContextStack(
        system_content="system prompt",
        raw_keep=2,
        compact_threshold_tokens=100_000,
    )


def test_default_raw_keep_is_eleven():
    # One more than the 10-round progress-note cadence, so dropped
    # rounds are always covered by progress.md.
    assert ContextStack(system_content="sys").raw_keep == 11


def test_raw_rounds_accumulate_under_budget(stack):
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", f"out-{index}"))
    stats = stack.maybe_advance(model="test-model", budget_tokens=999_999)
    assert stats["compacted"] is False
    assert len(stack.raw_rounds) == 6
    assert stack.dropped_round_count == 0


def test_compact_keeps_recent_rounds_and_drops_rest(stack):
    stack.compact_threshold_tokens = 40
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stats = stack.maybe_advance(model="test-model", budget_tokens=None)

    assert stats["compacted"] is True
    assert stack.dropped_round_count == 4
    assert len(stack.raw_rounds) == 2
    assert not any(
        str(m.get("content", "")).startswith("[CONTEXT_COMPACT]")
        for m in stack.to_messages()
    )


def test_second_compact_drops_again(stack):
    stack.compact_threshold_tokens = 40
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stack.maybe_advance(model="test-model", budget_tokens=None)
    assert stack.dropped_round_count == 4

    for index in range(6, 10):
        stack.complete_step(_tool_step(f"c{index}", "grep", "y" * 200))
    stack.maybe_advance(model="test-model", budget_tokens=None)

    assert stack.dropped_round_count == 8
    assert len(stack.raw_rounds) == 2


def test_no_compact_when_few_rounds_even_over_budget(stack):
    stack.compact_threshold_tokens = 1
    stack.start_turn("task")
    stack.complete_step(_tool_step("c0", "grep", "x" * 500))
    stack.complete_step(_tool_step("c1", "grep", "x" * 500))

    stats = stack.maybe_advance(model="test-model", budget_tokens=None)

    assert stats["compacted"] is False
    assert len(stack.raw_rounds) == 2


def test_user_message_attached_to_first_step_only(stack):
    stack.start_turn("hello")
    stack.complete_step(_tool_step("c0", "grep", "one"))
    stack.complete_step(_tool_step("c1", "grep", "two"))

    messages = stack.to_messages()
    user_contents = [m["content"] for m in messages if m.get("role") == "user"]
    assert user_contents.count("hello") == 1


def test_bootstrap_from_flat_messages(stack):
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "task"},
        *_tool_step("c0", "grep", "one"),
        *_tool_step("c1", "grep", "two"),
    ]
    stack.bootstrap_from_messages(messages)
    assert len(stack.raw_rounds) == 2
    rebuilt = stack.to_messages()
    assert rebuilt[0]["role"] == "system"
    assert any(m.get("call_id") == "c1" for m in rebuilt if m.get("type") == "function_call")


def test_bootstrap_skips_legacy_compact_prose(stack):
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "[CONTEXT_COMPACT]\nearlier work summary"},
        {"role": "user", "content": "task"},
        *_tool_step("c0", "grep", "one"),
    ]
    stack.bootstrap_from_messages(messages)
    assert len(stack.raw_rounds) == 1
    assert not any(
        str(m.get("content", "")).startswith("[CONTEXT_COMPACT]")
        for m in stack.to_messages()
    )


def test_bootstrap_preserves_trailing_user_message(stack):
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "Session progress\n\nCurrent request:\ncontinue"},
    ]
    stack.bootstrap_from_messages(messages)
    rebuilt = stack.to_messages()
    assert any(
        "Session progress" in m.get("content", "")
        for m in rebuilt
        if m.get("role") == "user"
    )


def test_subagent_state_appends_on_change_not_head_pin(stack):
    stack.set_progress_block("session progress")
    assert stack.set_subagent_state_block("- RUNNING: agent_worker 'task-1'")
    stack.start_turn("go")
    stack.complete_step(_tool_step("c0", "grep", "one"))

    contents = [str(m.get("content", "")) for m in stack.to_messages()]
    progress_i = next(i for i, c in enumerate(contents) if c.startswith("<progress>"))
    state_i = next(i for i, c in enumerate(contents) if c.startswith("<subagent_state>"))
    # Appended in raw rounds, after durable head pins — not rewritten into the head.
    assert state_i > progress_i
    assert any(c.startswith("<subagent_state>") and "task-1" in c for c in contents)
    assert stack.set_subagent_state_block("- RUNNING: agent_worker 'task-1'") is False

    assert stack.set_subagent_state_block(None)
    state_msgs = [
        str(m.get("content", ""))
        for m in stack.to_messages()
        if str(m.get("content", "")).startswith("<subagent_state>")
    ]
    # History stays (prefix-cache friendly); latest update clears RUNNING.
    assert len(state_msgs) >= 2
    assert "task-1" in state_msgs[0]
    assert "No subagent activity to report." in state_msgs[-1]
    assert stack.subagent_state_block is None


def test_bootstrap_migrates_legacy_head_subagent_state(stack):
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "<subagent_state>\n- No subagent is running\n</subagent_state>"},
        {"role": "user", "content": "task"},
        *_tool_step("c0", "grep", "one"),
    ]
    stack.bootstrap_from_messages(messages)
    assert stack.subagent_state_block == "- No subagent is running"
    # Legacy head pin becomes a tail raw round after the tool round.
    assert len(stack.raw_rounds) == 2
    assert any(
        str(m.get("content", "")).startswith("<subagent_state>")
        for m in stack.to_messages()
    )


def test_subagent_state_ignores_elapsed_only_changes(stack):
    assert stack.set_subagent_state_block(
        "- RUNNING: agent_worker 'task-1' (elapsed 1s); still going"
    )
    assert (
        stack.set_subagent_state_block(
            "- RUNNING: agent_worker 'task-1' (elapsed 12s); still going"
        )
        is False
    )
    assert stack.subagent_state_block.endswith("(elapsed 1s); still going")


def test_bootstrap_keeps_appended_subagent_state_rounds(stack):
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "task"},
        *_tool_step("c0", "grep", "one"),
        {
            "role": "user",
            "content": "<subagent_state>\n- RUNNING: agent_worker 'task-1'\n</subagent_state>",
        },
    ]
    stack.bootstrap_from_messages(messages)
    assert stack.subagent_state_block == "- RUNNING: agent_worker 'task-1'"
    assert any(
        str(m.get("content", "")).startswith("<subagent_state>") and "task-1" in str(m.get("content", ""))
        for m in stack.to_messages()
    )


def test_agent_context_manager_mutates_in_place():
    from langbridge_code.context.agent_context import AgentContextManager

    messages = [{"role": "system", "content": "sys"}]
    holder = messages
    context = AgentContextManager(system_content="sys", run_log_path=None, label="Worker")
    context.attach(messages)
    context.begin_turn("hello")
    assert holder is messages
    assert any(m.get("content") == "hello" for m in messages if m.get("role") == "user")


def test_maybe_advance_compacts_over_budget(stack):
    stack.compact_threshold_tokens = 40
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stats = stack.maybe_advance(model="test-model", budget_tokens=None)
    assert stats["compacted"] is True
    assert len(stack.raw_rounds) == 2


def test_pinned_assigned_task_in_every_to_messages(stack):
    stack.set_pinned_assigned_task("Fix login bug")
    stack.start_turn("implement fix")
    stack.complete_step(_tool_step("c0", "grep", "one"))

    messages = stack.to_messages()
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == f"{ASSIGNED_TASK_PREFIX}Fix login bug"
    assert not any(
        ASSIGNED_TASK_PREFIX in str(m.get("content", ""))
        for round_msgs in stack.raw_rounds
        for m in round_msgs
    )


def test_pinned_survives_compaction(stack):
    stack.compact_threshold_tokens = 40
    stack.set_pinned_assigned_task("Add retry logic")
    stack.start_turn("step prompt")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stack.maybe_advance(model="test-model", budget_tokens=None)

    messages = stack.to_messages()
    pinned = [m for m in messages if m.get("content", "").startswith(ASSIGNED_TASK_PREFIX)]
    assert len(pinned) == 1
    assert "Add retry logic" in pinned[0]["content"]
    assert stack.dropped_round_count == 4


def test_bootstrap_restores_pinned_assigned_task(stack):
    pinned = f"{ASSIGNED_TASK_PREFIX}Ship feature X"
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": pinned},
        {"role": "user", "content": "turn prompt"},
        *_tool_step("c0", "grep", "one"),
    ]
    stack.bootstrap_from_messages(messages)
    assert stack.pinned_user_content == pinned
    rebuilt = stack.to_messages()
    assert rebuilt[1]["content"] == pinned


def test_blocks_emitted_in_order_and_wrapped(stack):
    stack.set_memory_block("user prefers short replies")
    stack.set_progress_block("#### Key discoveries\n- built webpage")
    stack.set_skill_index_block("- grill-me: challenge assumptions")
    stack.start_turn("next task")
    stack.complete_step(_tool_step("c0", "grep", "one"))

    contents = [str(m.get("content", "")) for m in stack.to_messages()]
    memory_at = next(i for i, c in enumerate(contents) if c.startswith("<memory>"))
    progress_at = next(i for i, c in enumerate(contents) if c.startswith("<progress>"))
    skill_at = next(i for i, c in enumerate(contents) if c.startswith("<skill_index>"))
    task_at = contents.index("next task")
    # Head <progress> (resume/compaction only), then rounds.
    assert memory_at < progress_at < skill_at < task_at
    assert contents[memory_at].rstrip().endswith("</memory>")
    assert "built webpage" in contents[progress_at]
    assert contents[progress_at].rstrip().endswith("</progress>")


def test_set_block_none_or_blank_removes_it(stack):
    stack.set_memory_block("something")
    stack.set_memory_block("   ")
    stack.set_progress_block(None)
    contents = [str(m.get("content", "")) for m in stack.to_messages()]
    assert not any(c.startswith("<memory>") for c in contents)
    assert not any(c.startswith("<progress>") for c in contents)


def test_blocks_survive_compaction_and_callback_fires(stack):
    stack.compact_threshold_tokens = 40
    stack.set_memory_block("stale memory")
    stack.set_skill_index_block("- grill-me: x")
    fired = {}

    def refresh(inner_stack):
        fired["called"] = True
        inner_stack.set_memory_block("fresh memory")
        inner_stack.set_progress_block("## Turn 1\n- noted")

    stack.on_compacted = refresh
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stats = stack.maybe_advance(model="test-model", budget_tokens=None)

    assert stats["compacted"] is True
    assert fired.get("called") is True
    contents = [str(m.get("content", "")) for m in stack.to_messages()]
    memory = next(c for c in contents if c.startswith("<memory>"))
    assert "fresh memory" in memory and "stale memory" not in memory
    assert any(c.startswith("<progress>") for c in contents)
    assert any(c.startswith("<skill_index>") for c in contents)


def test_bootstrap_absorbs_block_messages(stack):
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "<memory>\nremembered fact\n</memory>"},
        {"role": "user", "content": "<progress>\n## Turn 1\n- did stuff\n</progress>"},
        {"role": "user", "content": "<skill_index>\n- grill-me: x\n</skill_index>"},
        {"role": "user", "content": "task"},
        *_tool_step("c0", "grep", "one"),
    ]
    stack.bootstrap_from_messages(messages)
    assert stack.memory_block == "remembered fact"
    assert "did stuff" in stack.progress_block
    assert stack.skill_index_block == "- grill-me: x"
    assert len(stack.raw_rounds) == 1
    # No duplicate block messages in the rebuilt transcript.
    contents = [str(m.get("content", "")) for m in stack.to_messages()]
    assert sum(1 for c in contents if c.startswith("<memory>")) == 1
