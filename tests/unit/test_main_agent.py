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
        "read_skill",
        "ask_user",
        "agent_planner",
        "agent_worker",
        "agent_explorer",
    } <= names


def test_subagent_planner_persists_task_type(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    run_log.write_text('{"summary": "", "turns": []}\n', encoding="utf-8")

    def fake_planner(*args, **kwargs):
        todo_path = run_log.with_name(f"{run_log.stem}.todo_list.md")
        todo_path.write_text("# Todo\n\n- [ ] Build auth\n", encoding="utf-8")
        return "PLAN_TASK_TYPE: coding\n\nPlan ready."

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
    assert "type=coding" in result
    from langbridge_code.agents.common.todo_list import read_task_type

    assert read_task_type(run_log) == "coding"


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
    run_log = tmp_path / "session.json"
    run_log.write_text(
        '{"summary": "", "turns": [{"turn_id": 1, "user": "build web", '
        '"assistant": "Built webpage."}]}\n',
        encoding="utf-8",
    )
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
    assert any("Session progress from prior turns" in content for content in user_messages)
    assert any("Built webpage" in content for content in user_messages)
    assert any("Recent session dialogue" in content for content in user_messages)
    assert any("Current request:\ncontinue" in content for content in user_messages)


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
