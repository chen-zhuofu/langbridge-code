from langbridge_code.agents.common import limits
from langbridge_code.tools.agent_worker_reviewer import run_worker_component
from langbridge_code.agents.common.limits import over_context_budget, over_time_budget
from langbridge_code.tools.agent_worker_reviewer import WorkerSession, ReviewerSession
from langbridge_code.agents.main_agent import run_agent_turn

READY = "WORKER_STATUS: READY_FOR_REVIEW\nSummary: implemented"


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


def test_specialist_keeps_running_over_context_budget(monkeypatch):
    """No context hard stop: the loop continues and relies on compaction."""

    def fake_response(api_key, model, messages, tool_schemas, label, **kwargs):
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": READY}]}
            ]
        }

    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.create_model_response", fake_response)
    monkeypatch.setattr("langbridge_code.context.common.budget.context_budget_tokens", lambda model, fraction=None: 1)

    session = WorkerSession("k", "m", [], {})
    session.messages.append({"role": "user", "content": "x" * 10_000})
    report = session.send("user")

    assert "exceeded the context budget" not in report
    assert "READY_FOR_REVIEW" in report


def test_specialist_stops_on_time_budget(monkeypatch):
    def fake_response(api_key, model, messages, tool_schemas, label, **kwargs):
        raise AssertionError("model should not be called once the time budget is gone")

    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.create_model_response", fake_response)
    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.MAX_REVIEWER_SECONDS", 0)

    session = ReviewerSession("k", "m", [], {})
    report = session.send("user")

    assert "out of time" in report


def test_workflow_stops_on_time_budget(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"

    class ExecSession:
        def __init__(self, *args, **kwargs):
            self.messages = kwargs.get("messages")

        def run_turn(self, prompt, **kwargs):
            from langbridge_code.tools.agent_worker_reviewer import build_agent_worker_tool

            agent_worker = build_agent_worker_tool(
                api_key="k",
                model="m",
                run_log_path=run_log,
                turn_id=1,
                messages=self.messages or [],
                target=prompt,
            )
            return agent_worker(
                prompt="Do work",
                description="run coding",
            )

    monkeypatch.setattr("langbridge_code.agents.main_agent.MainAgentSession", ExecSession)
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (False, "Worker/reviewer loop timed out."),
    )

    reply = run_agent_turn("k", "m", "hi", run_log, 1, print_reply=False)

    assert "stopped" in reply.lower()


def test_coder_stops_on_time_budget(monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (False, "Coder/reviewer loop timed out."),
    )

    output = run_worker_component("k", "m", {"task": "t", "context": "c"})

    assert "WORKFLOW_REVIEW_STATUS: NEEDS_WORK" in output
