"""Functional tests for the main-agent workflow (mock LLM boundaries only)."""

from langbridge_code.agents.main_agent import MainAgentSession, run_agent_turn


class _FakeMainSession:
    def __init__(self, reply, *, messages=None, **kwargs):
        self.messages = messages
        self.reply = reply

    def run_turn(self, prompt, **kwargs):
        return self.reply


def test_workflow_main_agent_direct_reply(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"

    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.MainAgentSession",
        lambda *args, **kwargs: _FakeMainSession(
            "Hello from LangBridge Code.",
            messages=kwargs.get("messages"),
            **kwargs,
        ),
    )

    reply = run_agent_turn("key", "model", "hi", run_log, 1, print_reply=False)
    assert reply == "Hello from LangBridge Code."


def test_workflow_delegation_run_coding(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    calls = []
    # Simulate a non-git workspace: the worker runs in place (no worktree).
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.worktree_mod.is_git_repo",
        lambda cwd=None: False,
    )

    class CodingSession(_FakeMainSession):
        def run_turn(self, prompt, **kwargs):
            from langbridge_code.tools.agent_worker_reviewer import build_agent_worker_tool

            agent_worker = build_agent_worker_tool(
                api_key="key",
                model="model",
                run_log_path=run_log,
                turn_id=1,
                messages=self.messages or [],
                target=prompt,
            )
            todo_path = run_log.parent / "todo_list.md"
            todo_path.write_text(
                "<!-- task_type: coding -->\n# Todo\n\n- [ ] Add widget\n",
                encoding="utf-8",
            )
            monkeypatch.setattr(
                "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
                lambda *args, **kwargs: calls.append(args) or (True, "Add widget done"),
            )
            return agent_worker(
                prompt="Add widget",
                description="run coding",
            )

    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.MainAgentSession",
        lambda *args, **kwargs: CodingSession("", messages=kwargs.get("messages"), **kwargs),
    )

    reply = run_agent_turn("key", "model", "add a widget", run_log, 1, print_reply=False)

    assert calls
    assert "Single-task completed" in reply
    assert "Add widget done" in reply


def test_workflow_delegation_plan_then_execute(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    planner_calls = []
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.worktree_mod.is_git_repo",
        lambda cwd=None: False,
    )

    class PlanThenRunSession(_FakeMainSession):
        def run_turn(self, prompt, **kwargs):
            from langbridge_code.tools.agent_worker_reviewer import build_agent_worker_tool
            from langbridge_code.tools.agent_planner import build_agent_planner_tool

            agent_planner = build_agent_planner_tool(
                api_key="key",
                model="model",
                run_log_path=run_log,
                turn_id=1,
            )
            agent_worker = build_agent_worker_tool(
                api_key="key",
                model="model",
                run_log_path=run_log,
                turn_id=1,
                messages=self.messages or [],
                target=prompt,
            )

            def fake_planner(*args, **kwargs):
                planner_calls.append(True)
                todo_path = run_log.parent / "todo_list.md"
                todo_path.write_text(
                    "<!-- task_type: coding -->\n# Todo\n\n- [ ] Build auth system\n",
                    encoding="utf-8",
                )
                return "PLAN_TASK_TYPE: coding\n\nReady."

            monkeypatch.setattr("langbridge_code.tools.agent_planner.run_planner", fake_planner)
            monkeypatch.setattr(
                "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
                lambda *args, **kwargs: (True, "Build auth system done"),
            )
            agent_planner(
                prompt="build auth",
                description="plan",
            )
            return agent_worker(
                prompt="Build auth system",
                description="run coding",
            )

    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.MainAgentSession",
        lambda *args, **kwargs: PlanThenRunSession("", messages=kwargs.get("messages"), **kwargs),
    )

    reply = run_agent_turn("key", "model", "build auth", run_log, 1, print_reply=False)

    assert planner_calls
    assert "Single-task completed" in reply


def test_workflow_worker_failure_returns_without_auto_refine(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    refine_calls = []

    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (False, "REVIEW_VERDICT: FAIL"),
    )
    monkeypatch.setattr(
        "langbridge_code.tools.agent_planner.run_planner",
        lambda *args, **kwargs: refine_calls.append(True),
    )

    from langbridge_code.tools.agent_worker_reviewer import build_agent_worker_tool

    agent_worker = build_agent_worker_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
        messages=[{"role": "system", "content": "sys"}],
        target="fix login",
    )
    reply = agent_worker(
        prompt="Fix login",
        description="worker",
        task_name="fix-login",
    )

    assert not refine_calls
    assert "stopped before approval" in reply
