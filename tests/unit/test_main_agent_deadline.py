from langbridge_code.agents import main_agent
from langbridge_code.agents.common import limits


def test_finalization_window_uses_transient_notice_and_removes_new_subagents(
    monkeypatch,
):
    monkeypatch.setattr(main_agent, "MAX_AGENT_SECONDS", 1620)
    monkeypatch.setattr(main_agent, "FINALIZE_RESERVE_SECONDS", 300)
    monkeypatch.setattr(limits, "now", lambda: 1320.1)

    assert main_agent.in_finalization_window(0.0) is True
    messages = [{"role": "system", "content": "stable"}]
    request = main_agent.request_messages(messages, "deepseek-v4-pro", finalizing=True)

    assert messages == [{"role": "system", "content": "stable"}]
    assert request[-1]["content"] == main_agent.FINALIZATION_NOTICE
    final_names = {schema["name"] for schema in main_agent.FINALIZATION_TOOL_SCHEMAS}
    assert {"agent_planner", "agent_worker", "agent_explorer"}.isdisjoint(final_names)


def test_finalization_window_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(main_agent, "FINALIZE_RESERVE_SECONDS", 0)
    assert main_agent.in_finalization_window(0.0) is False


def test_subagent_dispatch_is_code_blocked_after_window_opens(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main_agent, "MAX_AGENT_SECONDS", 1620)
    monkeypatch.setattr(main_agent, "FINALIZE_RESERVE_SECONDS", 300)
    monkeypatch.setattr(limits, "now", lambda: 1320.1)
    session = main_agent.MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "sys"}],
        tmp_path / "run.json",
        1,
    )
    session._turn_start_time = 0.0

    result = session._run_tool(
        {
            "name": "agent_worker",
            "call_id": "late-worker",
            "arguments": "{}",
        }
    )

    assert "disabled during the deadline finalization window" in result["output"]
    assert session._deadline_finalizing is True
