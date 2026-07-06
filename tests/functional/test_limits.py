from langbridge_code.agents import limits
from langbridge_code.agents.agent import run_l4_component
from langbridge_code.agents.limits import over_context_budget, over_time_budget
from langbridge_code.agents.multi_agent import run_specialist_agent
from langbridge_code.workflow.run import run_workflow

READY = "CODER_STATUS: READY_FOR_REVIEW\nSummary: implemented"


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

    monkeypatch.setattr("langbridge_code.agents.multi_agent.create_specialist_response", fake_response)
    monkeypatch.setattr("langbridge_code.agents.multi_agent.MAX_SPECIALIST_CONTEXT_TOKENS", 1)

    report = run_specialist_agent("k", "m", "system", "user", [], {}, "Coder")

    assert report.startswith("CODER_STATUS: IN_PROGRESS")
    assert "exceeded the context budget" in report


def test_specialist_stops_on_time_budget(monkeypatch):
    def fake_response(api_key, model, messages, tool_schemas, label):
        raise AssertionError("model should not be called once the time budget is gone")

    monkeypatch.setattr("langbridge_code.agents.multi_agent.create_specialist_response", fake_response)
    monkeypatch.setattr("langbridge_code.agents.multi_agent.MAX_SPECIALIST_SECONDS", 0)

    report = run_specialist_agent("k", "m", "system", "user", [], {}, "Reviewer")

    assert "ran out of time" in report


def test_workflow_stops_on_time_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.workflow.run.MAX_WORKFLOW_SECONDS", 0)
    monkeypatch.setattr(
        "langbridge_code.workflow.run.route",
        lambda *args, **kwargs: {
            "kind": "task",
            "reply": "",
            "hard": False,
            "task_type": "coding",
            "task_summary": "Do work",
        },
    )

    reply = run_workflow("k", "m", "hi", tmp_path / "run.json", 1, print_reply=False)

    assert "could not complete" in reply.lower() or "stopped" in reply.lower()


def test_l4_compat_stops_on_time_budget(monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.agents.agent.run_coder_reviewer_loop",
        lambda *args, **kwargs: (False, "Coder/reviewer loop timed out."),
    )

    output = run_l4_component("k", "m", {"task": "t", "context": "c"})

    assert "WORKFLOW_REVIEW_STATUS: NEEDS_WORK" in output
