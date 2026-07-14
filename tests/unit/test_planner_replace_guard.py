from langbridge_code.tools.todo_list import update_plan
from langbridge_code.tools.agent_planner import (
    build_agent_planner_tool,
    planner_replace_blocked_message,
)
from langbridge_code.tools import todo_list as plan_mod


def _write_unfinished_todo(run_log):
    update_plan(
        "# Todo\n\n- [x] Done step\n- [ ] Build auth\n- [ ] Add tests\n",
        run_log_path=run_log,
    )


def test_planner_replace_guard_message_lists_unfinished_items(tmp_path):
    run_log = tmp_path / "run.json"
    _write_unfinished_todo(run_log)
    message = planner_replace_blocked_message(run_log)
    assert message is not None
    assert "2 item(s) remaining" in message
    assert "Build auth" in message
    assert "replace_existing_plan=true" in message


def test_planner_replace_guard_allows_empty_todo(tmp_path):
    run_log = tmp_path / "run.json"
    assert planner_replace_blocked_message(run_log) is None


def test_agent_planner_blocked_without_replace_flag(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    _write_unfinished_todo(run_log)
    monkeypatch.setattr(
        "langbridge_code.tools.agent_planner.run_planner",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planner should not run")),
    )
    tool = build_agent_planner_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
    )
    result = tool(prompt="build payments", description="plan")
    assert "unfinished todo_list" in result
    assert "replace_existing_plan=true" in result


def test_agent_planner_allows_replace_when_confirmed(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    _write_unfinished_todo(run_log)
    calls = []

    def fake_planner(*args, **kwargs):
        calls.append(True)
        return "PLAN_TASK_TYPE: coding\n\nNew plan."

    monkeypatch.setattr("langbridge_code.tools.agent_planner.run_planner", fake_planner)
    tool = build_agent_planner_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
    )
    result = tool(
        prompt="build payments",
        description="plan",
        replace_existing_plan=True,
    )
    assert calls == [True]
    assert "DRAFT" in result
    assert "Suggested PLAN_TASK_TYPE: coding" in result
    assert "update_plan" in result


def test_agent_planner_allowed_after_clear_plan(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    _write_unfinished_todo(run_log)
    plan_mod.clear_plan(run_log_path=run_log)
    calls = []

    def fake_planner(*args, **kwargs):
        calls.append(True)
        return "PLAN_TASK_TYPE: slide\n\nNew plan."

    monkeypatch.setattr("langbridge_code.tools.agent_planner.run_planner", fake_planner)
    tool = build_agent_planner_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
    )
    result = tool(prompt="build deck", description="plan")
    assert calls == [True]
    assert "DRAFT" in result
    assert "Suggested PLAN_TASK_TYPE: slide" in result
