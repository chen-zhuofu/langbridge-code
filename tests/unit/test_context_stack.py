import pytest

from langbridge_code.context.common.stack import (
    ASSIGNED_TASK_PREFIX,
    COMPACT_PROSE_PREFIX,
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


def _fake_prose_compactor(api_key, model, *, compact_prose, rounds, label=""):
    parts = []
    if compact_prose:
        parts.append(compact_prose)
    parts.append(f"{len(rounds)} rounds folded")
    return "compact prose: " + " | ".join(parts)


@pytest.fixture
def stack():
    return ContextStack(
        system_content="system prompt",
        raw_keep=2,
        compact_fraction=0.4,
        prose_compactor=_fake_prose_compactor,
    )


def test_default_raw_keep_is_eleven():
    # One more than the 10-round progress-note cadence, so the compressed
    # middle always overlaps progress.md.
    assert ContextStack(system_content="sys").raw_keep == 11


def test_raw_rounds_accumulate_under_budget(stack):
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", f"out-{index}"))
    stats = stack.maybe_advance(api_key="k", model="test-model", budget_tokens=999_999)
    assert stats["prose_compacted"] is False
    assert len(stack.raw_rounds) == 6
    assert stack.compact_prose is None


def test_compact_keeps_recent_rounds_and_folds_rest(stack, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.context.common.stack.model_context_window",
        lambda _model: 100,
    )
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stats = stack.maybe_advance(api_key="k", model="test-model", budget_tokens=40)

    assert stats["prose_compacted"] is True
    assert stack.compact_prose is not None
    assert "4 rounds folded" in stack.compact_prose
    assert len(stack.raw_rounds) == 2
    assert COMPACT_PROSE_PREFIX in stack.to_messages()[1]["content"]


def test_second_compact_merges_prior_prose(stack, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.context.common.stack.model_context_window",
        lambda _model: 100,
    )
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stack.maybe_advance(api_key="k", model="test-model", budget_tokens=40)
    first_prose = stack.compact_prose

    for index in range(6, 10):
        stack.complete_step(_tool_step(f"c{index}", "grep", "y" * 200))
    stack.maybe_advance(api_key="k", model="test-model", budget_tokens=40)

    assert first_prose in stack.compact_prose
    assert len(stack.raw_rounds) == 2
    # Only one compact prose message in the transcript.
    prose_messages = [
        m for m in stack.to_messages()
        if str(m.get("content", "")).startswith(COMPACT_PROSE_PREFIX)
    ]
    assert len(prose_messages) == 1


def test_no_compact_when_few_rounds_even_over_budget(stack, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.context.common.stack.model_context_window",
        lambda _model: 10,
    )
    stack.start_turn("task")
    stack.complete_step(_tool_step("c0", "grep", "x" * 500))
    stack.complete_step(_tool_step("c1", "grep", "x" * 500))

    stats = stack.maybe_advance(api_key="k", model="test-model", budget_tokens=1)

    assert stats["prose_compacted"] is False
    assert len(stack.raw_rounds) == 2


def test_prose_compression_persisted_to_debug(stack, tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.context.debug.CONTEXT_DEBUG_PERSIST", True)
    monkeypatch.setattr(
        "langbridge_code.context.common.stack.model_context_window",
        lambda _model: 100,
    )
    session_dir = tmp_path / "session-test-2026-07-09T120000"
    session_dir.mkdir()
    (session_dir / "traces").mkdir()
    (session_dir / "debug" / "2026-07-09T120000.00").mkdir(parents=True)
    from langbridge_code.util.agent_debug import set_agent_debug
    from langbridge_code.util.trace_log import begin_trace

    begin_trace(session_dir, "2026-07-09T120000.00")
    set_agent_debug("Worker", 1)
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stack.maybe_advance(api_key="k", model="test-model", budget_tokens=40)

    debug_dir = session_dir / "debug" / "2026-07-09T120000.00"
    files = list(debug_dir.glob("worker_1_prose_*_output.md"))
    assert len(files) == 1
    assert "rounds folded" in files[0].read_text(encoding="utf-8")


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


def test_bootstrap_restores_compact_prose(stack):
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": COMPACT_PROSE_PREFIX + "earlier work summary"},
        {"role": "user", "content": "task"},
        *_tool_step("c0", "grep", "one"),
    ]
    stack.bootstrap_from_messages(messages)
    assert stack.compact_prose == "earlier work summary"
    assert len(stack.raw_rounds) == 1


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


def test_agent_context_manager_mutates_in_place():
    from langbridge_code.context.agent_context import AgentContextManager

    messages = [{"role": "system", "content": "sys"}]
    holder = messages
    context = AgentContextManager(system_content="sys", run_log_path=None, label="Worker")
    context.attach(messages)
    context.begin_turn("hello")
    assert holder is messages
    assert any(m.get("content") == "hello" for m in messages if m.get("role") == "user")


def test_maybe_advance_noop_without_api_key(stack):
    stack.start_turn("task")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", f"out-{index}"))
    stats = stack.maybe_advance(api_key=None, model=None)
    assert stats["prose_compacted"] is False
    assert len(stack.raw_rounds) == 6


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


def test_pinned_survives_compaction(stack, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.context.common.stack.model_context_window",
        lambda _model: 100,
    )
    stack.set_pinned_assigned_task("Add retry logic")
    stack.start_turn("step prompt")
    for index in range(6):
        stack.complete_step(_tool_step(f"c{index}", "grep", "x" * 200))
    stack.maybe_advance(api_key="k", model="test-model", budget_tokens=40)

    messages = stack.to_messages()
    pinned = [m for m in messages if m.get("content", "").startswith(ASSIGNED_TASK_PREFIX)]
    assert len(pinned) == 1
    assert "Add retry logic" in pinned[0]["content"]
    assert stack.compact_prose is not None


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
    stack.set_progress_block("## Turn 1\n- built webpage")
    stack.set_skill_index_block("- grill-me: challenge assumptions")
    stack.start_turn("next task")
    stack.complete_step(_tool_step("c0", "grep", "one"))

    contents = [str(m.get("content", "")) for m in stack.to_messages()]
    memory_at = next(i for i, c in enumerate(contents) if c.startswith("<memory>"))
    progress_at = next(i for i, c in enumerate(contents) if c.startswith("<progress>"))
    skill_at = next(i for i, c in enumerate(contents) if c.startswith("<skill_index>"))
    task_at = contents.index("next task")
    assert memory_at < progress_at < skill_at < task_at
    assert contents[memory_at].rstrip().endswith("</memory>")
    assert "built webpage" in contents[progress_at]


def test_set_block_none_or_blank_removes_it(stack):
    stack.set_memory_block("something")
    stack.set_memory_block("   ")
    stack.set_progress_block(None)
    contents = [str(m.get("content", "")) for m in stack.to_messages()]
    assert not any(c.startswith("<memory>") for c in contents)
    assert not any(c.startswith("<progress>") for c in contents)


def test_blocks_survive_compaction_and_callback_fires(stack, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.context.common.stack.model_context_window",
        lambda _model: 100,
    )
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
    stats = stack.maybe_advance(api_key="k", model="test-model", budget_tokens=40)

    assert stats["prose_compacted"] is True
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
