from langbridge_code.agents.system_prompt import reviewer_system_prompt, worker_system_prompt
from langbridge_code.tools.agent_worker_reviewer import (
    new_reviewer_session,
    new_worker_session,
)


def test_worker_and_reviewer_schemas_include_memory_writer():
    worker = new_worker_session("key", "model", task_name="t1")
    reviewer = new_reviewer_session("key", "model", task_name="t1")
    assert any(schema["name"] == "memory_writer" for schema in worker.tool_schemas)
    assert any(schema["name"] == "memory_writer" for schema in reviewer.tool_schemas)
    assert "memory_writer" in worker.tools
    assert "memory_writer" in reviewer.tools


def test_worker_and_reviewer_prompts_mention_memory_writer():
    assert "memory_writer" in worker_system_prompt("coding")
    assert "<memory>" in worker_system_prompt("coding")
    assert "memory_writer" in reviewer_system_prompt("coding")
    assert "<memory>" in reviewer_system_prompt("coding")


def test_worker_begin_send_prefetches_memory(monkeypatch):
    calls = []

    def fake_prefetch(api_key, model, task, **kwargs):
        calls.append(task)
        return "user prefers terse diffs"

    monkeypatch.setattr("langbridge_code.memory.prefetch_memory", fake_prefetch)
    session = new_worker_session("key", "model")
    session.begin_send("implement auth", assigned_task="## Task\nDo auth")
    assert calls == ["## Task\nDo auth"]
    assert session.context.stack.memory_block == "user prefers terse diffs"
    pinned = "\n".join(
        str(message.get("content", ""))
        for message in session.messages
        if isinstance(message, dict)
    )
    assert "<memory>" in pinned or session.context.stack.memory_block


def test_worker_memory_writer_skips_end_schedule(monkeypatch):
    calls = {"writer": 0, "scheduled": 0, "model": 0}

    def fake_response(*args, **kwargs):
        calls["model"] += 1
        if calls["model"] == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "memory_writer",
                        "call_id": "m1",
                        "arguments": '{"purpose":"save durable fact"}',
                    }
                ]
            }
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "done\nWORKER_STATUS: READY_FOR_REVIEW",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.create_model_response",
        fake_response,
    )
    monkeypatch.setattr(
        "langbridge_code.memory.run_memory_writer_agent",
        lambda *a, **k: calls.__setitem__("writer", calls["writer"] + 1) or "ok",
    )
    monkeypatch.setattr(
        "langbridge_code.memory.schedule_memory_writer",
        lambda *a, **k: calls.__setitem__("scheduled", calls["scheduled"] + 1),
    )
    monkeypatch.setattr("langbridge_code.memory.prefetch_memory", lambda *a, **k: "")
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.write_worklog_received",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.write_worklog_step",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.write_worklog_observation",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.write_worklog_finish",
        lambda *a, **k: None,
    )

    session = new_worker_session("key", "model")
    reply = session.send("do it", assigned_task="task")
    assert "READY_FOR_REVIEW" in reply
    assert calls["writer"] == 1
    assert calls["scheduled"] == 0


def test_worker_phase_end_schedules_when_unused(monkeypatch):
    calls = {"scheduled": 0}

    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.create_model_response",
        lambda *a, **k: {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "done\nWORKER_STATUS: READY_FOR_REVIEW",
                        }
                    ],
                }
            ]
        },
    )
    monkeypatch.setattr(
        "langbridge_code.memory.schedule_memory_writer",
        lambda *a, **k: calls.__setitem__("scheduled", calls["scheduled"] + 1),
    )
    monkeypatch.setattr("langbridge_code.memory.prefetch_memory", lambda *a, **k: "")
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.write_worklog_received",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.write_worklog_finish",
        lambda *a, **k: None,
    )

    session = new_worker_session("key", "model")
    session.send("do it", assigned_task="task")
    assert calls["scheduled"] == 1


def test_reviewer_phase_end_schedules_when_unused(monkeypatch):
    calls = {"scheduled": 0}

    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.create_model_response",
        lambda *a, **k: {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "ok\nREVIEW_VERDICT: PASS",
                        }
                    ],
                }
            ]
        },
    )
    monkeypatch.setattr(
        "langbridge_code.memory.schedule_memory_writer",
        lambda *a, **k: calls.__setitem__("scheduled", calls["scheduled"] + 1),
    )
    monkeypatch.setattr("langbridge_code.memory.prefetch_memory", lambda *a, **k: "")
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.write_worklog_received",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.write_worklog_finish",
        lambda *a, **k: None,
    )

    session = new_reviewer_session("key", "model")
    session.send("review it", assigned_task="task")
    assert calls["scheduled"] == 1
