from langbridge_code.agents.common.todo_list import (
    TodoTask,
    format_dependency_dispatch_guidance,
    parse_depends_indices,
    ready_task_indices,
    resolved_depends_indices,
)
from langbridge_code.tools.agent_worker_reviewer import next_parallel_batch
from langbridge_code.tools.todo_list import read_plan


def test_parse_depends_none_and_list():
    assert parse_depends_indices(TodoTask("A <!-- depends: none -->")) == []
    assert parse_depends_indices(TodoTask("B <!-- depends: 1, 2 -->")) == [1, 2]
    assert parse_depends_indices(TodoTask("C")) is None


def test_resolved_depends_defaults_to_previous():
    tasks = [
        TodoTask("A"),
        TodoTask("B"),
        TodoTask("C <!-- depends: none -->"),
    ]
    assert resolved_depends_indices(tasks, 0) == []
    assert resolved_depends_indices(tasks, 1) == [1]
    assert resolved_depends_indices(tasks, 2) == []


def test_ready_wave_for_independent_then_dependent():
    from langbridge_code.agents.common.todo_list import clean_task_text

    tasks = [
        TodoTask("One <!-- depends: none -->"),
        TodoTask("Two <!-- depends: none -->"),
        TodoTask("Three <!-- depends: 1, 2 -->"),
    ]
    assert ready_task_indices(tasks) == [0, 1]
    batch = next_parallel_batch(tasks, 4)
    assert [clean_task_text(t.description) for t in batch] == ["One", "Two"]

    tasks[0].done = True
    tasks[1].done = True
    assert ready_task_indices(tasks) == [2]
    assert next_parallel_batch(tasks, 4) == []


def test_read_plan_includes_dependency_dispatch(tmp_path, monkeypatch):
    from langbridge_code.agents.common import todo_list as todo_mod

    plan = tmp_path / "todo_list.md"
    plan.write_text(
        "# Todo\n\n"
        "- [ ] Add auth <!-- depends: none -->\n"
        "- [ ] Add billing <!-- depends: none -->\n"
        "- [ ] Wire together <!-- depends: 1, 2 -->\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(todo_mod, "todo_list_path", lambda run_log_path=None: plan)
    monkeypatch.setattr(
        "langbridge_code.tools.todo_list.worktree_mod.ready_branches",
        lambda _path: [],
    )
    text = read_plan(run_log_path=tmp_path / "run.json")
    assert "Ready now:" in text
    assert "Blocked:" in text
    assert "depends: 1, 2" in text
    assert "waiting on: 1, 2" in text


def test_format_guidance_after_partial_complete():
    tasks = [
        TodoTask("One <!-- depends: none -->", done=True),
        TodoTask("Two <!-- depends: none -->"),
        TodoTask("Three <!-- depends: 1, 2 -->"),
    ]
    text = format_dependency_dispatch_guidance(tasks)
    assert "2. Two" in text
    assert "waiting on: 2" in text
