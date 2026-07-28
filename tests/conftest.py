"""Eval subprocess helpers — plus hermetic seams for prefetch/fork LLM calls.

Sessions prefetch memory and skill indexes (one-pass LLM) and fork note/memory
writers on their live context. Unit tests construct sessions with fake API keys,
so these helpers are stubbed out by default to keep tests offline. Tests that
exercise the real helpers override the ``_no_llm_prefetch`` fixture locally
(and monkeypatch ``create_model_response`` themselves).
"""
import pytest


@pytest.fixture(autouse=True)
def _no_llm_prefetch(monkeypatch):
    import langbridge_code.agents.common.fork as fork_mod
    import langbridge_code.skills as skills_mod
    import langbridge_code.memory as memory_mod
    import langbridge_code.tools.memory_writer as memory_writer_mod

    monkeypatch.setattr(memory_mod, "prefetch_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        memory_writer_mod, "schedule_memory_writer", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        skills_mod,
        "select_skill_index",
        lambda api_key, model, task, catalog, **kwargs: (catalog or "").strip(),
    )
    monkeypatch.setattr(fork_mod, "fork_one_pass", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        fork_mod,
        "fork_progress_note",
        lambda *args, **kwargs: "Progress note fork made no file changes; nothing recorded.",
    )
    yield


@pytest.fixture(autouse=True)
def _isolated_plan_file(monkeypatch, tmp_path):
    """Keep the session plan file (todo_list.md) out of the real workspace root."""
    import langbridge_code.agents.common.todo_list as todo_list_mod

    monkeypatch.setattr(todo_list_mod, "plan_path", lambda: tmp_path / "todo_list.md")
    yield


@pytest.fixture(autouse=True)
def _isolated_worktree_git(monkeypatch, tmp_path):
    """Point worktree git operations away from the developer's real repo.

    Without this, any test that dispatches a coding task without mocking
    ``is_git_repo`` creates real branches/worktrees in the langbridge-code
    checkout (e.g. the stale ``lb/run.json/fix-login`` debris). Tests that
    need real git monkeypatch ``worktree_mod.WORKSPACE_ROOT`` to their own
    temp repo, which overrides this default.
    """
    import langbridge_code.agents.common.worktree as worktree_mod

    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir(exist_ok=True)
    monkeypatch.setattr(worktree_mod, "WORKSPACE_ROOT", not_a_repo)
    monkeypatch.setattr(worktree_mod, "AGENT_STATE_DIR", tmp_path / "agent-state")
    yield
