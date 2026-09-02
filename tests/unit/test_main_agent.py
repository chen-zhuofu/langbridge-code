import json
import threading

from langbridge_code.agents.common.workspace import workspace_scope
from langbridge_code.agents.main_agent import (
    MAIN_AGENT_TOOL_SCHEMAS,
    MainAgentSession,
    ensure_langbridge_system_prompt,
)
from langbridge_code.agents.planner import build_agent_planner_tool


def test_ensure_langbridge_system_prompt_inserts_system_message():
    messages = ensure_langbridge_system_prompt([])
    assert messages[0]["role"] == "system"
    assert "LangBridge" in messages[0]["content"]


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
        "browser",
        "read_skill",
        "ask_user",
        "agent_planner",
        "agent_worker",
        "agent_explorer",
        "memory_writer",
    } <= names
    assert "run_tests" not in names


def test_tool_search_is_main_session_only_and_available_for_gmail_schedule(tmp_path):
    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        tmp_path / "run.json",
        1,
        allowed_tool_names={"gmail"},
    )
    assert {schema["name"] for schema in session.tool_schemas} == {"ToolSearch"}
    # It is not part of the shared schemas passed to forked/subagent harnesses.
    assert "ToolSearch" not in {schema["name"] for schema in MAIN_AGENT_TOOL_SCHEMAS}


def test_obsidian_vault_is_injected_as_a_dynamic_system_reminder(tmp_path, monkeypatch):
    first_vault = tmp_path / "First Vault"
    second_vault = tmp_path / "Second Vault"
    first_vault.mkdir()
    second_vault.mkdir()
    selected = {"path": first_vault}
    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.obsidian_vault_path",
        lambda: selected["path"],
    )
    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        tmp_path / "run.json",
        1,
    )

    session._refresh_obsidian_vault_context()
    initial_round_count = len(session.context.stack.raw_rounds)
    initial = session.context.stack.to_messages()[-1]["content"]
    assert initial.startswith("<system-reminder>")
    assert str(first_vault) in initial
    assert "Do not ask the user for the Vault path again" in initial

    session._refresh_obsidian_vault_context()
    assert len(session.context.stack.raw_rounds) == initial_round_count

    selected["path"] = second_vault
    session._refresh_obsidian_vault_context()
    changed = session.context.stack.to_messages()[-1]["content"]
    assert "Obsidian Vault configuration changed" in changed
    assert str(second_vault) in changed

    selected["path"] = None
    session._refresh_obsidian_vault_context()
    removed = session.context.stack.to_messages()[-1]["content"]
    assert "no Vault is currently selected" in removed


def test_tool_search_loads_schema_and_mcp_call_uses_registry(tmp_path):
    class FakeRegistry:
        def __init__(self):
            self.loaded = []

        def search(self, query):
            assert query == "select:gmail.search_threads"
            self.loaded = [{
                "type": "function",
                "name": "mcp__gmail__search_threads",
                "parameters": {"type": "object", "properties": {}},
            }]
            return {"selected": "gmail.search_threads", "tools": self.loaded}

        def loaded_schemas(self):
            return self.loaded

        def is_loaded_call(self, name):
            return name == "mcp__gmail__search_threads"

        def call(self, name, arguments):
            assert arguments == {"query": "newer_than:1d"}
            return "gmail result"

    session = object.__new__(MainAgentSession)
    session._turn_start_time = None
    session._deadline_finalizing = False
    session.approval_callback = None
    session.run_log_path = tmp_path
    session.tools = {}
    session.mcp_registry = FakeRegistry()
    session._fixed_tool_schemas = [{"name": "ToolSearch"}]

    selected = session._run_tool({
        "name": "ToolSearch",
        "call_id": "search",
        "arguments": '{"query":"select:gmail.search_threads"}',
    })
    assert "mcp__gmail__search_threads" in selected["output"]
    assert {schema["name"] for schema in session.tool_schemas} == {
        "ToolSearch", "mcp__gmail__search_threads"
    }

    called = session._run_tool({
        "name": "mcp__gmail__search_threads",
        "call_id": "gmail",
        "arguments": '{"query":"newer_than:1d"}',
    })
    assert called["output"] == "gmail result"


def test_openai_native_tool_search_is_executed_by_client(tmp_path, monkeypatch):
    class FakeRegistry:
        def __init__(self):
            self.loaded = []

        def available_tool_names(self):
            return ("gmail.search_threads",)

        def search(self, query):
            assert query == "select:gmail.search_threads"
            self.loaded = [{
                "type": "function",
                "name": "mcp__gmail__search_threads",
                "parameters": {"type": "object", "properties": {}},
            }]
            return {"selected": "gmail.search_threads", "tools": self.loaded}

        def native_search_tools(self, selection):
            return [{**selection["tools"][0], "defer_loading": True}]

        def loaded_schemas(self):
            return self.loaded

        def is_loaded_call(self, _name):
            return False

    calls = []

    def fake_response(_api_key, _model, agent_input, **kwargs):
        calls.append({"input": list(agent_input), "tools": kwargs["tool_schemas"]})
        if len(calls) == 1:
            return {"output": [{
                "type": "tool_search_call",
                "call_id": "search-1",
                "arguments": '{"query":"select:gmail.search_threads"}',
            }]}
        outputs = [item for item in agent_input if item.get("type") == "tool_search_output"]
        assert outputs[0]["call_id"] == "search-1"
        assert outputs[0]["execution"] == "client"
        assert outputs[0]["tools"][0]["defer_loading"] is True
        return {"output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": "Loaded."}],
        }]}

    monkeypatch.setattr("langbridge_code.agents.main_agent.create_model_response", fake_response)
    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_observation", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
    monkeypatch.setattr(
        "langbridge_code.tools.memory_writer.schedule_memory_writer",
        lambda *a, **k: "scheduled",
    )

    session = MainAgentSession(
        "key",
        "gpt-5.6",
        [{"role": "system", "content": "sys"}],
        tmp_path / "run.json",
        1,
        allowed_tool_names={"gmail"},
    )
    session.mcp_registry = FakeRegistry()
    session._sync_deferred_tool_schemas()
    session._context_blocks_ready = True

    assert session.send("Search Gmail") == "Loaded."
    assert calls[0]["tools"] == [
        next(schema for schema in session._fixed_tool_schemas if schema["type"] == "tool_search")
    ]
    assert not any(tool.get("name") == "mcp__gmail__search_threads" for tool in calls[1]["tools"])

    session.context.stack.raw_rounds = []
    session._restore_native_tool_search_history()
    restored = [item for values in session.context.stack.raw_rounds for item in values]
    assert [item["type"] for item in restored] == [
        "tool_search_call",
        "tool_search_output",
    ]


def test_browser_screenshot_tool_marks_image_for_next_model_step(tmp_path):
    screenshot = tmp_path / "browser.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    session = object.__new__(MainAgentSession)
    session._turn_start_time = None
    session._deadline_finalizing = False
    session.approval_callback = None
    session.run_log_path = tmp_path
    session.tools = {
        "browser": lambda **_arguments: json.dumps(
            {"image_paths": [str(screenshot)], "screenshot_path": str(screenshot)}
        )
    }

    result = session._run_tool(
        {
            "name": "browser",
            "call_id": "browser-call",
            "arguments": '{"description":"capture X","action":"screenshot"}',
        }
    )

    assert result["_image_paths"] == [str(screenshot)]
    assert result["type"] == "function_call_output"


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

    monkeypatch.setattr("langbridge_code.agents.planner.run_planner", fake_planner)

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


def test_main_agent_send_appends_final_reply(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    messages = [{"role": "system", "content": "sys"}]

    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)
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
    reply = session.send("go")
    assert reply == "Done."
    assert session.messages[-1] == {"role": "assistant", "content": "Done."}


def test_memory_writer_tool_schedules_and_skips_end_hook(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    calls = {"model": 0, "tool_scheduled": 0, "end_scheduled": 0}

    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
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
                        "arguments": '{"description":"save user correction"}',
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

    def fake_schedule(api_key, model, messages):
        calls["tool_scheduled"] += 1
        assert any("不清楚就问我" in str(message) for message in messages)
        return "Memory Writer scheduled."

    def counting_schedule(api_key, model, messages):
        # Distinguish mid-turn tool schedule from end-of-turn catch-up.
        if calls["tool_scheduled"] == 0:
            return fake_schedule(api_key, model, messages)
        calls["end_scheduled"] += 1
        return "Memory Writer scheduled."

    monkeypatch.setattr("langbridge_code.agents.main_agent.create_model_response", fake_response)
    monkeypatch.setattr(
        "langbridge_code.tools.memory_writer.schedule_memory_writer",
        counting_schedule,
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

    assert session.send("不清楚就问我") == "Done."
    assert calls == {"model": 2, "tool_scheduled": 1, "end_scheduled": 0}


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
                    '{"description":"write plan","path":"todo_list.md",'
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
                    '{"description":"mark done","path":"todo_list.md",'
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
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
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
    reply = session.send("go")

    assert reply == "All workers handled."
    assert model_round == 3


def test_session_init_reconciles_stale_working_registry(tmp_path):
    from langbridge_code.agents.common import worktree as worktree_mod

    run_log = tmp_path / "run.json"
    worktree_mod.record_branch(
        run_log,
        worktree_mod.WorktreeInfo(
            branch="lb/session/task-2-levels",
            path=tmp_path / "task-2-levels",
            task_description="Build levels.js",
            task_name="task-2-levels",
        ),
        "working",
    )
    worktree_mod.record_branch(
        run_log,
        worktree_mod.WorktreeInfo(
            branch="lb/session/task-3-sprites",
            path=tmp_path / "task-3-sprites",
            task_description="Build sprites.js",
            task_name="task-3-sprites",
        ),
        "ready",
    )

    session = MainAgentSession(
        "key", "model", [{"role": "system", "content": "sys"}], run_log, 1
    )

    statuses = {
        item["task_name"]: item["status"]
        for item in worktree_mod.registry_snapshot(run_log)
    }
    assert statuses == {"task-2-levels": "failed", "task-3-sprites": "ready"}
    block = session.context.stack.subagent_state_block
    assert "No subagent is running in this process right now." in block
    assert "task-2-levels [failed]" in block
    assert "task-3-sprites [ready]" in block


def test_subagent_state_block_shows_running_worker_during_send(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    release_slow = threading.Event()
    model_round = 0

    monkeypatch.setattr("langbridge_code.agents.main_agent.emit_phase", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_received", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_observation", lambda *a, **k: None)

    def _subagent_state_messages(messages):
        return [
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict)
            and str(message.get("content", "")).startswith("<subagent_state>")
        ]

    def fake_response(*args, **kwargs):
        nonlocal model_round
        model_round += 1
        current_messages = kwargs.get("messages") or args[2]
        states = _subagent_state_messages(current_messages)
        if model_round == 1:
            assert states == []
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "agent_worker",
                        "call_id": "slow",
                        "arguments": '{"description":"slow","task_name":"task-slow"}',
                    },
                    {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "quick",
                        "arguments": '{"command":"true"}',
                    },
                ]
            }
        if model_round == 2:
            # The worker is still pending: the latest tail update must say RUNNING.
            assert states
            assert "RUNNING: agent_worker 'task-slow'" in states[-1]
            release_slow.set()
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Waiting."}],
                    }
                ]
            }
        # After the worker completed, the latest update must not claim RUNNING.
        # Older appends may still mention it (prefix-cache friendly history).
        assert states
        assert "RUNNING: agent_worker" not in states[-1]
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done."}],
                }
            ]
        }

    monkeypatch.setattr("langbridge_code.agents.main_agent.create_model_response", fake_response)
    session = MainAgentSession(
        "key", "model", [{"role": "system", "content": "sys"}], run_log, 1, target="go"
    )
    session._context_blocks_ready = True

    def fake_run_tool(call):
        if call["call_id"] == "slow":
            release_slow.wait(timeout=2)
        return {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": f"{call['call_id']} result",
        }

    session._run_tool = fake_run_tool
    assert session.send("go") == "Done."
    assert model_round == 3
    # send() finished: the block must not claim anything is running.
    block = session.context.stack.subagent_state_block
    assert block is None or "RUNNING" not in block


def test_main_agent_session_injects_session_context(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    from langbridge_code.util.progress import SESSION_MEMORY_HEADER, write_progress

    write_progress(run_log, SESSION_MEMORY_HEADER + "## Turn 1\n- Built webpage\n")

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
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
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
    reply = session.send("continue")

    assert reply == "Continuing."
    user_messages = [m["content"] for m in captured["messages"] if m.get("role") == "user"]
    progress_blocks = [content for content in user_messages if content.startswith("<session_memory>")]
    assert progress_blocks and "Built webpage" in progress_blocks[0]
    assert progress_blocks[0].rstrip().endswith("</session_memory>")
    assert "continue" in user_messages


def test_main_agent_first_send_prepends_prior_conversation(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    from langbridge_code.util.progress import SESSION_MEMORY_HEADER, write_progress
    from langbridge_code.util.session_traces import append_session_memory_boundary, append_raw_round

    write_progress(run_log, SESSION_MEMORY_HEADER + "## Turn 1\n- Built webpage\n")
    append_raw_round(run_log, 1, [{"role": "user", "content": "make a webpage"}])
    append_session_memory_boundary(run_log, 1)
    append_raw_round(
        run_log,
        2,
        [
            {"role": "assistant", "content": "styling the header"},
            {
                "type": "function_call",
                "name": "run_shell",
                "call_id": "c1",
                "arguments": "{}",
            },
        ],
    )

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
    assert session.send("continue") == "Resumed."

    user_contents = [
        m.get("content")
        for m in captured["messages"]
        if m.get("role") == "user"
    ]
    history_blocks = [
        c for c in user_contents if str(c).startswith("<history_conversation>")
    ]
    assert len(history_blocks) == 1
    history = history_blocks[0]
    assert "USER: make a webpage" in history
    assert "ASSISTANT: styling the header" in history
    assert history.rstrip().endswith("</history_conversation>")
    # Current turn is a plain user message, not inside the history tag.
    assert "continue" in user_contents
    assert "USER: continue" not in history
    # Prior chat is not replayed as bare user/assistant turns.
    assert ("user", "make a webpage") not in [
        (m.get("role"), m.get("content")) for m in captured["messages"]
    ]
    assert not any(m.get("type") == "function_call" for m in captured["messages"])
    progress_blocks = [c for c in user_contents if str(c).startswith("<session_memory>")]
    assert progress_blocks and "Built webpage" in progress_blocks[0]


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
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
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
    assert session.send("first") == "Reply 1"
    session.bind_turn(2, target="second")
    assert session.send("second") == "Reply 2"

    assert len(captured) == 2
    # Second turn pins turn-1 Q&A inside <history_conversation>, plus new user.
    history_blocks = [
        m.get("content")
        for m in captured[1]
        if str(m.get("content", "")).startswith("<history_conversation>")
    ]
    assert len(history_blocks) == 1
    assert "USER: first" in history_blocks[0]
    assert "ASSISTANT: Reply 1" in history_blocks[0]
    assert any(m.get("content") == "second" for m in captured[1])
    assert "USER: second" not in history_blocks[0]
    # Pinned blocks are set once; the second turn does not duplicate them.
    progress_blocks = [
        m for m in captured[1] if str(m.get("content", "")).startswith("<session_memory>")
    ]
    assert len(progress_blocks) <= 1


def test_progress_note_forced_after_quiet_rounds(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    monkeypatch.setattr("langbridge_code.agents.main_agent.SESSION_MEMORY_REMINDER_ROUNDS", 2)

    captured = []
    forced = {"count": 0}

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
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
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

    def fake_force_note():
        forced["count"] += 1
        return "Noted in session_memory.md: stub"

    session._write_session_memory_via_fork = fake_force_note
    assert session.send("go") == "Done."

    # Rounds 1-2 stay quiet; after round 3 (> 2) code force-writes session_memory.md.
    assert forced["count"] == 1
    assert len(captured) == 4


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


def test_fork_first_init_reuses_memory_snapshot_without_prefetch(monkeypatch, tmp_path):
    from langbridge_code.util.session import fork_session
    import langbridge_code.memory as memory_mod

    source = tmp_path / "session-source"
    source.mkdir()
    forked = fork_session(source, memory_context="## user/style.md\n原 session 选中的记忆")
    prefetch_tasks = []

    def fake_prefetch(_api_key, _model, task):
        prefetch_tasks.append(task)
        return "newly prefetched memory"

    monkeypatch.setattr(memory_mod, "prefetch_memory", fake_prefetch)

    forked_session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        forked,
        2,
    )
    forked_session._init_context_blocks("a different fork question")

    assert prefetch_tasks == []
    assert forked_session.context.stack.memory_block == (
        "## user/style.md\n原 session 选中的记忆"
    )
    assert not (forked / ".fork-memory-context.md").exists()

    normal = tmp_path / "session-normal"
    normal.mkdir()
    normal_session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        normal,
        1,
    )
    normal_session._init_context_blocks("ordinary resume question")

    assert prefetch_tasks == ["ordinary resume question"]


def test_fork_first_init_does_not_prefetch_when_memory_snapshot_is_empty(
    monkeypatch, tmp_path
):
    from langbridge_code.util.session import fork_session
    import langbridge_code.memory as memory_mod

    source = tmp_path / "session-source"
    source.mkdir()
    forked = fork_session(source, memory_context="")

    def unexpected_prefetch(*_args, **_kwargs):
        raise AssertionError("fork initialization must not prefetch memory")

    monkeypatch.setattr(memory_mod, "prefetch_memory", unexpected_prefetch)

    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        forked,
        1,
    )
    session._init_context_blocks("fork question")

    assert session.context.stack.memory_block is None
    assert not (forked / ".fork-memory-context.md").exists()


def test_update_session_memory_tool_forks_edit_writer(monkeypatch, tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    import langbridge_code.agents.common.fork as fork_mod

    fork_seen = {}

    def fake_fork(api_key, model, messages, **kwargs):
        from langbridge_code.util.progress import ensure_session_memory_template, write_progress

        fork_seen["messages"] = list(messages)
        fork_seen["run_log_path"] = kwargs.get("run_log_path")
        ensure_session_memory_template(run_log)
        write_progress(
            run_log,
            "# Session memory\n\n#### Key discoveries\n_desc_\n\n- Fixed the parser; tests pass.\n",
        )
        return "Noted in session_memory.md: Fixed the parser; tests pass."

    monkeypatch.setattr(fork_mod, "fork_session_memory", fake_fork)

    calls = {"n": 0}

    def fake_response(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "c1",
                        "name": "update_session_memory",
                        "arguments": '{"description": "record"}',
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
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_observation", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)

    session = MainAgentSession("key", "model", [{"role": "system", "content": "sys"}], run_log, 1)
    assert session.send("go") == "Done."

    from langbridge_code.util.progress import read_progress

    progress = read_progress(run_log)
    assert "Fixed the parser; tests pass." in progress
    # Mid-turn note updates the file only — does not inject into <session_memory>.
    assert not (session.context.stack.progress_block or "")
    assert fork_seen["run_log_path"] == run_log
    # Counter was reset by the note; only the post-step increment remains.
    assert session._rounds_since_session_memory <= 1


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
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)
    monkeypatch.setattr("langbridge_code.agents.main_agent.write_worklog_finish", lambda *a, **k: None)

    session = MainAgentSession("key", "model", messages, None, 1, target="what is this?")
    reply = session.send("what is this?")
    assert reply == "Just an answer."
    assert session.messages[-1] == {"role": "assistant", "content": "Just an answer."}
