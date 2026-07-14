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
        "list_dir",
        "glob",
        "read_file",
        "read_many",
        "grep",
        "edit_file",
        "write",
        "multi_edit",
        "apply_patch",
        "delete_file",
        "run_tests",
        "bash",
        "powershell",
        "git_status",
        "git_diff",
        "git_commit",
        "lsp",
        "read_webpage",
        "browse_webpage",
        "read_plan",
        "clear_plan",
        "update_plan",
        "read_skill",
        "ask_user",
        "agent_planner",
        "agent_worker",
        "agent_explorer",
    } <= names


def test_subagent_planner_returns_draft_without_committing(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    run_log.write_text('{"summary": "", "turns": []}\n', encoding="utf-8")

    def fake_planner(*args, **kwargs):
        return (
            "PLAN_TASK_TYPE: coding\n\n"
            "```markdown\n"
            "# Plan: Auth\n\n"
            "## Todo list\n"
            "- [ ] Build auth <!-- depends: none -->\n"
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
    assert "update_plan" in result
    assert "ask the user" in result.lower() or "ask_user" in result
    assert "Suggested PLAN_TASK_TYPE: coding" in result
    from langbridge_code.agents.common.todo_list import load_tasks

    assert load_tasks(run_log) == []


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
