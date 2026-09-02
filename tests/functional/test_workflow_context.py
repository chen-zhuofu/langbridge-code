"""Workflow tests for main agent session context and todo resume."""

from langbridge_code.agents.main_agent import MainAgentSession, run_agent_turn


def _write_todo(run_log_path, lines, task_type="coding"):
    content = f"<!-- task_type: {task_type} -->\n# Todo\n\n" + "\n".join(lines) + "\n"
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    (run_log_path.parent / f"{run_log_path.stem}.todo_list.md").write_text(content, encoding="utf-8")


class _FakeMainSession:
    def __init__(self, reply, *, messages=None, **kwargs):
        self.messages = messages
        self.reply = reply
        self.prompts = []

    def send(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.reply


def test_workflow_uses_main_agent_session(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    captured = {}

    def fake_session(*args, **kwargs):
        captured["messages"] = kwargs.get("messages") or args[2]
        return _FakeMainSession("Direct answer.", messages=captured["messages"], **kwargs)

    monkeypatch.setattr("langbridge_code.agents.main_agent.MainAgentSession", fake_session)

    history = [
        {"role": "system", "content": "You are LangBridge."},
        {"role": "user", "content": "你现在是新版了"},
        {"role": "assistant", "content": "是的，已更新。"},
    ]
    reply = run_agent_turn(
        "key",
        "model",
        "继续吧",
        run_log,
        1,
        print_reply=False,
        messages=history,
    )

    assert reply == "Direct answer."
    assert captured["messages"] is history


def test_workflow_resume_can_execute_existing_todo(tmp_path, monkeypatch):
    run_log = tmp_path / "run.json"
    _write_todo(run_log, ["- [ ] Build a web game"])
    from langbridge_code.agents.common import worktree as worktree_mod

    info = worktree_mod.WorktreeInfo(
        "lb/run/task-game",
        tmp_path / "wt",
        "Build a web game",
        task_name="task-game",
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.ensure_git_repo",
        lambda cwd=None: tmp_path,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.resumable_worktree",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.create_worktree",
        lambda *args, **kwargs: info,
    )
    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.worktree_mod.record_branch",
        lambda *args, **kwargs: None,
    )

    class ResumeSession(_FakeMainSession):
        def send(self, prompt, **kwargs):
            from langbridge_code.agents.worker_reviewer import build_agent_worker_tool

            agent_worker = build_agent_worker_tool(
                api_key="key",
                model="model",
                run_log_path=run_log,
                turn_id=1,
                messages=self.messages or [],
                target=prompt,
            )
            return agent_worker(
                prompt="Build a web game",
                description="run coding",
                task_name="task-game",
            )

    monkeypatch.setattr(
        "langbridge_code.agents.worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (True, "Build a web game done"),
    )
    monkeypatch.setattr(
        "langbridge_code.agents.main_agent.MainAgentSession",
        lambda *args, **kwargs: ResumeSession("", messages=kwargs.get("messages"), **kwargs),
    )

    reply = run_agent_turn(
        "key",
        "model",
        "继续吧",
        run_log,
        1,
        print_reply=False,
        messages=[{"role": "system", "content": "sys"}],
    )

    assert "Worktree task completed" in reply
