from langbridge_cli.agents import limits
from langbridge_cli.agents.agent import run_pm_loop, run_l4_component
from langbridge_cli.agents.limits import over_context_budget, over_time_budget
from langbridge_cli.agents.multi_agent import run_specialist_agent

READY = "L4_STATUS: READY_FOR_REVIEW\nSummary: implemented"


def test_over_time_budget_uses_elapsed(monkeypatch):
    clock = iter([100.0, 100.0, 105.0, 130.0])
    monkeypatch.setattr(limits, "now", lambda: next(clock))

    start = limits.now()  # 100
    assert over_time_budget(start, 10) is False  # elapsed 0
    assert over_time_budget(start, 10) is False  # elapsed 5
    assert over_time_budget(start, 10) is True   # elapsed 30


def test_budgets_disabled_when_none():
    assert over_time_budget(0.0, None) is False
    assert over_context_budget([{"role": "user", "content": "x" * 10_000}], None) is False


def test_over_context_budget_compares_estimated_tokens():
    messages = [{"role": "user", "content": "hello there"}]
    assert over_context_budget(messages, 100_000) is False
    assert over_context_budget(messages, 1) is True


def test_specialist_stops_on_context_budget(monkeypatch):
    def fake_response(api_key, model, messages, tool_schemas, label):
        raise AssertionError("model should not be called once the context budget is gone")

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.create_specialist_response", fake_response)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.MAX_SPECIALIST_CONTEXT_TOKENS", 1)

    report = run_specialist_agent("k", "m", "system", "user", [], {}, "L4 engineer")

    assert report.startswith("L4_STATUS: IN_PROGRESS")
    assert "exceeded the context budget" in report


def test_specialist_stops_on_time_budget(monkeypatch):
    def fake_response(api_key, model, messages, tool_schemas, label):
        raise AssertionError("model should not be called once the time budget is gone")

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.create_specialist_response", fake_response)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.MAX_SPECIALIST_SECONDS", 0)

    report = run_specialist_agent("k", "m", "system", "user", [], {}, "L3 test engineer")

    assert "ran out of time" in report


def test_pm_agent_stops_on_time_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.agents.agent.MAX_AGENT_SECONDS", 0)

    finished = run_pm_loop(
        "k",
        "m",
        "hi",
        tmp_path / "run.json",
        1,
        print_reply=False,
    )

    assert finished == "Agent stopped because it ran out of time."


def test_l4_l3_loop_stops_on_time_budget(monkeypatch):
    def fake_l3(*args, **kwargs):
        raise AssertionError("L3 should not run once the time budget is gone")

    def fake_l4(api_key, model, task, context, feedback="", **kwargs):
        return READY

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l3_test_engineer", fake_l3)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l4_engineer", fake_l4)
    monkeypatch.setattr("langbridge_cli.agents.agent.MAX_L4_L3_SECONDS", 0)

    output = run_l4_component("k", "m", {"task": "t", "context": "c"})

    assert "PM_REVIEW_STATUS: NEEDS_WORK" in output
