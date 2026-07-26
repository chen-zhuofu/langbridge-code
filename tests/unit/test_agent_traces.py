import json
from types import SimpleNamespace

from langbridge_code.context.agent_context import AgentContextManager, finish_step
from langbridge_code.agents.worker_reviewer import new_reviewer_session
from langbridge_code.util.agent_traces import (
    append_agent_raw_round,
    append_compaction_event,
    build_agent_resume_background,
    reserve_agent_trace,
)


def _jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_trace_instance_ids_start_at_zero_and_increment_per_role_task(tmp_path):
    first, first_id = reserve_agent_trace(tmp_path, "Worker", "task-3-api")
    second, second_id = reserve_agent_trace(tmp_path, "Worker", "task-3-api")
    other, other_id = reserve_agent_trace(tmp_path, "Worker", "task-4-ui")

    assert first_id == 0
    assert second_id == 1
    assert other_id == 0
    assert first == tmp_path / "task-3-api" / "worker-0.md"
    assert second == tmp_path / "task-3-api" / "worker-1.md"
    assert other == tmp_path / "task-4-ui" / "worker-0.md"


def test_agent_trace_keeps_full_raw_round(tmp_path):
    path, _ = reserve_agent_trace(tmp_path, "Explore", "find-auth-flow")
    messages = [
        {"role": "user", "content": "inspect auth"},
        {"type": "function_call", "name": "read_file", "arguments": '{"path":"auth.py"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "x" * 10_000},
    ]
    append_agent_raw_round(path, round_index=0, messages=messages)

    content = path.read_text(encoding="utf-8")
    assert content.startswith("# Explore trace — task: find-auth-flow (instance 0)")
    assert "## Round 0" in content
    payload = json.loads(content.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload == messages
    assert len(payload[2]["output"]) == 10_000


def test_resume_background_reads_prior_dispatch_but_not_current_trace(tmp_path):
    prior, _ = reserve_agent_trace(tmp_path, "Worker", "task-3-api")
    append_agent_raw_round(
        prior,
        round_index=0,
        messages=[
            {"role": "user", "content": "implement applications API"},
            {"role": "assistant", "content": "PUT still needs review"},
        ],
    )
    current, _ = reserve_agent_trace(tmp_path, "Worker", "task-3-api")

    background = build_agent_resume_background(
        tmp_path,
        role="Worker",
        task_name="task-3-api",
        model="kimi-k2.7-code",
        progress="older progress",
        exclude_trace=current,
    )

    assert "implement applications API" in background
    assert "PUT still needs review" in background


def test_reviewer_session_keeps_task_name_for_trace_resume(tmp_path):
    session = new_reviewer_session(
        "key",
        "kimi-k2.7-code",
        run_log_path=tmp_path,
        task_name="task-3-api",
    )

    assert session.context.task_name == "task-3-api"
    assert session.context.agent_trace_path == tmp_path / "task-3-api" / "reviewer-0.md"


def test_full_compaction_event_moves_to_attachment(tmp_path):
    # append_compaction_event is used by progress-note merge compaction.
    path = append_compaction_event(
        tmp_path,
        {
            "type": "progress_compaction",
            "role": "LangBridge",
            "task_name": None,
            "instance_id": None,
            "before": {"tokens": 10_000, "turn_section_count": 20},
            "input": {"progress_markdown": "x" * 8_000},
            "output": {"progress_markdown": "merged"},
            "after": {"tokens": 2_000, "turn_section_count": 3},
        },
    )

    record = _jsonl(path)[0]
    assert record["before"]["tokens"] == 10_000
    assert record["after"]["tokens"] == 2_000
    attachment = path.parent / record["full_event_attachment"]
    full = json.loads(attachment.read_text(encoding="utf-8"))
    assert len(full["input"]["progress_markdown"]) == 8_000
    assert full["output"]["progress_markdown"] == "merged"


def test_context_compaction_drops_rounds_without_audit(tmp_path):
    manager = AgentContextManager(
        system_content="system",
        run_log_path=tmp_path,
        label="Worker",
        task_name="task-3-api",
    )
    messages = []
    manager.attach(messages)
    manager.stack.raw_keep = 1
    manager.stack.compact_threshold_tokens = 1

    manager.begin_turn("first prompt")
    manager.after_tool_step(
        [{"role": "assistant", "content": "first result"}],
        model="kimi-k2.7-code",
        budget_tokens=None,
    )
    manager.begin_turn("second prompt")
    manager.after_tool_step(
        [{"role": "assistant", "content": "second result"}],
        model="kimi-k2.7-code",
        budget_tokens=None,
    )

    assert manager.stack.dropped_round_count == 1
    assert len(manager.stack.raw_rounds) == 1
    # Dropping rounds writes no compaction audit; raw traces already have them.
    assert not (tmp_path / "compactions.jsonl").exists()


def test_finish_step_persists_subagent_round(tmp_path):
    manager = AgentContextManager(
        system_content="system",
        run_log_path=tmp_path,
        label="Planner",
        task_name="plan-interview-tool",
    )
    messages = []
    manager.attach(messages)
    manager.begin_turn("make a plan")
    session = SimpleNamespace(
        api_key=None,
        model="kimi-k2.7-code",
        run_log_path=tmp_path,
        label="Planner",
        turn_id=1,
    )
    finish_step(
        manager,
        [{"role": "assistant", "content": "the complete plan"}],
        session,
        budget=100_000,
    )

    trace = tmp_path / "plan-interview-tool" / "planner-0.md"
    content = trace.read_text(encoding="utf-8")
    assert "## Round 0" in content
    payload = json.loads(content.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert payload == [
        {"role": "user", "content": "make a plan"},
        {"role": "assistant", "content": "the complete plan"},
    ]
