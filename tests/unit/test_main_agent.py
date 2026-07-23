import threading

from langbridge_code.agents.common.workspace import workspace_scope
from langbridge_code.agents.main_agent import (
    MAIN_AGENT_TOOL_SCHEMAS,
    MainAgentSession,
    ensure_langbridge_system_prompt,
)
from langbridge_code.tools.agent_planner import build_agent_planner_tool


def test_ensure_langbridge_system_prompt_inserts_system_message():
    messages = ensure_langbridge_system_prompt([])
    assert messages[0]["role"] == "system"
    assert "LangBridge Code" in messages[0]["content"]


def test_main_agent_tool_schemas_include_full_toolkit_and_subagents():
    names = {schema["name"] for schema in MAIN_AGENT_TOOL_SCHEMAS}
    assert {
        "glob",
        "read_file",
        "grep",
        "Edit",
        "write",
        "bash",
        "powershell",
        "read_webpage",
        "read_skill",
        "ask_user",
        "agent_planner",
        "agent_worker",
        "agent_explorer",
        "memory_writer",
    } <= names
    assert "run_tests" not in names


def test_subagent_planner_returns_draft_without_committing(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    run_log.write_text('{"summary": "", "turns": []}\n', encoding="utf-8")

    def fake_planner(*args, **kwargs):
        return (
            "PLAN_TASK_TYPE: coding\n\n"
            "```markdown\n"
            "# Plan: Auth\n\n"
            "## Todo list\n"
            "- [ ] Build auth (verify: pytest tests/test_auth.py -v)\n"
            "```\n\n"
            "## Summary\nPlan ready.\n"
        )

    monkeypatch.setattr("langbridge_code.tools.agent_planner.run_planner", fake_planner)

    tools = {
        "agent_planner": build_agent_planner_tool(
            api_key="key",
            model="model",
            run_log_path=run_log,
            turn_id=1,
        ),
    }
    result = tools["agent_planner"](
        prompt="build auth",
        description="plan",
    )
    assert "DRAFT" in result
    assert "todo_list.md" in result
    assert "ask the user" in result.lower() or "ask_user" in result
    assert "Suggested PLAN_TASK_TYPE" not in result
    # The planner never writes the plan file itself.
    assert not (tmp_path / "todo_list.md").exists()


def test_main_agent_run_turn_does_not_finalize_locally(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    messages = [{"role": "system", "content": "sys"}]
    logged = {"finalize": False}

    def fake_finalize(*args, **kwargs):
        logged["finalize"] = True

    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)
    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.finalize_main_agent_turn",
        fake_finalize,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.create_model_response",
        lambda *args, **kwargs: {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done."}],
                }
            ]
        },
    )
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)

    session = MainAgentSession("key", "model", messages, run_log, 1, target="go")
    reply = session.run_turn("go")
    assert reply == "Done."
    assert not logged["finalize"]
    assert session.messages[-1] == {"role": "assistant", "content": "Done."}


def test_memory_writer_tool_forks_live_context_and_skips_end_hook(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    calls = {"model": 0, "writer": 0, "scheduled": 0}

    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_step", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_observation", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)

    def fake_response(*args, **kwargs):
        calls["model"] += 1
        if calls["model"] == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "memory_writer",
                        "call_id": "memory-1",
                        "arguments": '{"purpose":"save user correction"}',
                    }
                ]
            }
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done."}],
                }
            ]
        }

    def fake_writer(api_key, model, messages):
        calls["writer"] += 1
        assert any("不清楚就问我" in str(message) for message in messages)
        return "Updated feedback memory."

    monkeypatch.setattr("langbridge_code.agents.main_agent.create_model_response", fake_response)
    monkeypatch.setattr("langbridge_code.memory.run_memory_writer_agent", fake_writer)
    monkeypatch.setattr(
        "langbridge_code.memory.schedule_memory_writer",
        lambda *args, **kwargs: calls.__setitem__("scheduled", calls["scheduled"] + 1),
    )
    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        run_log,
        1,
        target="不清楚就问我",
    )
    session._context_blocks_ready = True

    assert session.run_turn("不清楚就问我") == "Done."
    assert calls == {"model": 2, "writer": 1, "scheduled": 0}


def test_plan_file_lives_only_in_session_artifacts(tmp_path, monkeypatch):
    from langbridge_code.agents.common import todo_list

    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "session-artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    monkeypatch.setattr(todo_list, "plan_path", lambda: workspace / "todo_list.md")

    with workspace_scope(workspace):
        session = MainAgentSession(
            "key",
            "model",
            [{"role": "system", "content": "sys"}],
            artifacts,
            1,
            target="build",
        )
        session._run_tool(
            {
                "name": "write",
                "call_id": "write-plan",
                "arguments": (
                    '{"purpose":"write plan","path":"todo_list.md",'
                    '"content":"- [ ] Task 1\\n"}'
                ),
            }
        )
        assert (artifacts / "todo_list.md").read_text(encoding="utf-8") == "- [ ] Task 1\n"
        assert not (workspace / "todo_list.md").exists()

        session._run_tool(
            {
                "name": "Edit",
                "call_id": "tick-plan",
                "arguments": (
                    '{"purpose":"mark done","path":"todo_list.md",'
                    '"old_string":"- [ ]","new_string":"- [x]"}'
                ),
            }
        )
        assert (artifacts / "todo_list.md").read_text(encoding="utf-8") == "- [x] Task 1\n"
        assert not (workspace / "todo_list.md").exists()


def test_session_start_migrates_legacy_workspace_plan(tmp_path, monkeypatch):
    from langbridge_code.agents.common import todo_list

    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "session-artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    legacy = workspace / "todo_list.md"
    legacy.write_text("- [ ] Legacy task\n", encoding="utf-8")
    monkeypatch.setattr(todo_list, "plan_path", lambda: legacy)

    with workspace_scope(workspace):
        MainAgentSession(
            "key",
            "model",
            [{"role": "system", "content": "sys"}],
            artifacts,
            1,
            target="continue",
        )

    assert not legacy.exists()
    assert (artifacts / "todo_list.md").read_text(encoding="utf-8") == "- [ ] Legacy task\n"


def test_main_agent_handles_first_worker_result_while_another_runs(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    messages = [{"role": "system", "content": "sys"}]
    release_slow = threading.Event()
    model_round = 0

    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_step", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_observation", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)

    def fake_response(*args, **kwargs):
        nonlocal model_round
        model_round += 1
        current_messages = kwargs.get("messages") or args[2]
        rendered = str(current_messages)
        if model_round == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "agent_worker",
                        "call_id": "slow",
                        "arguments": '{"description":"slow","task_name":"slow"}',
                    },
                    {
                        "type": "function_call",
                        "name": "agent_worker",
                        "call_id": "fast",
                        "arguments": '{"description":"fast","task_name":"fast"}',
                    },
                ]
            }
        if model_round == 2:
            assert "fast result" in rendered
            assert "Background task started and is still running" in rendered
            assert not release_slow.is_set()
            release_slow.set()
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Handled fast result."}],
                    }
                ]
            }
        assert "<background_tool_results>" in rendered
        assert "slow result" in rendered
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "All workers handled."}],
                }
            ]
        }

    monkeypatch.setattr("langbridge_code.agents.main_agent.create_model_response", fake_response)
    session = MainAgentSession("key", "model", messages, run_log, 1, target="go")
    session._context_blocks_ready = True

    def fake_run_tool(call):
        if call["call_id"] == "slow":
            release_slow.wait(timeout=1)
        return {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": f"{call['call_id']} result",
        }

    session._run_tool = fake_run_tool
    reply = session.run_turn("go")

    assert reply == "All workers handled."
    assert model_round == 3


def test_main_agent_session_injects_session_context(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    from langbridge_code.util.progress import PROGRESS_HEADER, write_progress

    write_progress(run_log, PROGRESS_HEADER + "## Turn 1\n- Built webpage\n")

    captured = {}

    def fake_response(*args, **kwargs):
        captured["messages"] = kwargs.get("messages") or args[2]
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Continuing."}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.create_model_response",
        fake_response,
    )
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_step", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)

    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        run_log,
        2,
        target="continue",
    )
    reply = session.run_turn("continue")

    assert reply == "Continuing."
    user_messages = [m["content"] for m in captured["messages"] if m.get("role") == "user"]
    progress_blocks = [content for content in user_messages if content.startswith("<progress>")]
    assert progress_blocks and "Built webpage" in progress_blocks[0]
    assert progress_blocks[0].rstrip().endswith("</progress>")
    assert "continue" in user_messages


def test_main_agent_first_send_uses_full_traces_when_they_fit(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    from langbridge_code.util.progress import PROGRESS_HEADER, write_progress
    from langbridge_code.util.session_traces import append_progress_boundary, append_raw_round

    write_progress(run_log, PROGRESS_HEADER + "## Turn 1\n- Built webpage\n")
    append_raw_round(run_log, 1, [{"role": "user", "content": "make a webpage"}])
    append_progress_boundary(run_log, 1)
    append_raw_round(run_log, 2, [{"role": "assistant", "content": "styling the header"}])

    captured = {}

    def fake_response(*args, **kwargs):
        captured["messages"] = kwargs.get("messages") or args[2]
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Resumed."}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.create_model_response",
        fake_response,
    )
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)

    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        run_log,
        3,
        target="continue",
    )
    assert session.run_turn("continue") == "Resumed."

    user_messages = [m["content"] for m in captured["messages"] if m.get("role") == "user"]
    progress_blocks = [c for c in user_messages if c.startswith("<progress>")]
    assert progress_blocks
    block = progress_blocks[0]
    # Small session: the full raw traces fit the resume budget, so they replace
    # the progress summary entirely.
    assert "make a webpage" in block
    assert "styling the header" in block
    assert "Built webpage" not in block


def test_main_agent_reuses_messages_across_turns(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    captured = []

    def fake_response(*args, **kwargs):
        messages = kwargs.get("messages") or args[2]
        captured.append([dict(m) for m in messages if m.get("role") in {"user", "assistant"}])
        turn_n = len(captured)
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": f"Reply {turn_n}"}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.create_model_response",
        fake_response,
    )
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_step", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)

    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        run_log,
        1,
        target="first",
        history_briefing_pending=False,
    )
    assert session.run_turn("first") == "Reply 1"
    session.bind_turn(2, target="second")
    assert session.run_turn("second") == "Reply 2"

    assert len(captured) == 2
    # Second turn still sees the first turn's user+assistant messages.
    assert any(m.get("content") == "first" for m in captured[1])
    assert any(m.get("content") == "Reply 1" for m in captured[1])
    assert any(m.get("content") == "second" for m in captured[1])
    # Pinned blocks are set once; the second turn does not duplicate them.
    progress_blocks = [
        m for m in captured[1] if str(m.get("content", "")).startswith("<progress>")
    ]
    assert len(progress_blocks) <= 1


def test_progress_note_reminder_injected_after_quiet_rounds(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    monkeypatch.setattr("langbridge_code.agents.main_agent.PROGRESS_NOTE_REMINDER_ROUNDS", 2)

    captured = []

    def fake_response(*args, **kwargs):
        messages = kwargs.get("messages") or args[2]
        captured.append([dict(m) for m in messages])
        if len(captured) <= 3:
            call_n = len(captured)
            return {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": f"c{call_n}",
                        "name": "no_such_tool",
                        "arguments": "{}",
                    }
                ]
            }
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done."}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.create_model_response",
        fake_response,
    )
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_step", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_observation", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)

    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        run_log,
        1,
        target="go",
        history_briefing_pending=False,
    )
    assert session.run_turn("go") == "Done."

    # Rounds 1-2 stay quiet; after round 3 (> 2) the hook lands before call 4.
    third_call_users = [m.get("content", "") for m in captured[2] if m.get("role") == "user"]
    assert not any("[HOOK]" in str(c) for c in third_call_users)
    fourth_call_users = [m.get("content", "") for m in captured[3] if m.get("role") == "user"]
    assert any("[HOOK]" in str(c) and "note_progress" in str(c) for c in fourth_call_users)


def test_main_agent_first_send_sets_memory_and_skill_blocks(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    import langbridge_code.memory as memory_mod

    monkeypatch.setattr(
        memory_mod, "prefetch_memory", lambda api_key, model, task: "## user/style.md\n偏好简短回复"
    )

    captured = {}

    def fake_response(*args, **kwargs):
        captured["messages"] = kwargs.get("messages") or args[2]
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}
            ]
        }

    monkeypatch.setattr("langbridge_code.agents.main_agent.create_model_response", fake_response)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)

    session = MainAgentSession("key", "model", [{"role": "system", "content": "sys"}], run_log, 1)
    assert session.send("do the thing") == "ok"

    contents = [str(m.get("content", "")) for m in captured["messages"]]
    memory_blocks = [c for c in contents if c.startswith("<memory>")]
    assert memory_blocks and "偏好简短回复" in memory_blocks[0]
    skill_blocks = [c for c in contents if c.startswith("<skill_index>")]
    assert skill_blocks and "grill" in skill_blocks[0]
    # Blocks precede the live user prompt.
    assert contents.index(memory_blocks[0]) < contents.index("do the thing")


def test_note_progress_tool_forks_note_writer(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    import langbridge_code.agents.common.fork as fork_mod

    fork_seen = {}

    def fake_fork(api_key, model, messages, instruction, **kwargs):
        fork_seen["instruction"] = instruction
        fork_seen["messages"] = list(messages)
        return "Fixed the parser; tests pass."

    monkeypatch.setattr(fork_mod, "fork_one_pass", fake_fork)

    calls = {"n": 0}

    def fake_response(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "name": "note_progress",
                        "arguments": '{"purpose": "record"}',
                    }
                ]
            }
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Done."}]}
            ]
        }

    monkeypatch.setattr("langbridge_code.agents.main_agent.create_model_response", fake_response)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_step", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_observation", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)

    session = MainAgentSession("key", "model", [{"role": "system", "content": "sys"}], run_log, 1)
    assert session.send("go") == "Done."

    from langbridge_code.util.progress import read_progress

    progress = read_progress(run_log)
    assert "Fixed the parser; tests pass." in progress
    # The fork got the live context plus one instruction.
    assert "note-writer" in fork_seen["instruction"]
    # Counter was reset by the note; only the post-step increment remains.
    assert session._rounds_since_progress_note <= 1


def test_main_agent_session_returns_direct_reply(monkeypatch):
    messages = [{"role": "system", "content": "sys"}]

    def fake_response(*args, **kwargs):
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Just an answer."}],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.create_model_response",
        fake_response,
    )
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_step", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)

    session = MainAgentSession("key", "model", messages, None, 1, target="what is this?")
    reply = session.send("what is this?")
    assert reply == "Just an answer."
    assert session.messages[-1] == {"role": "assistant", "content": "Just an answer."}
