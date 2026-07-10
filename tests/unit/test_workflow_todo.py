from langbridge_code.agents.common import todo_list as common
from langbridge_code.tools import todo_list as plan_mod
from langbridge_code.tools.agent_planner import format_unfinished_todo_summary, persist_task_type
from langbridge_code.tools.agent_worker_reviewer import mark_done, save_tasks, task_verify_command


def test_parse_and_render_todo_list():
    content = """# Todo

- [ ] Add login form
- [x] Demo deck
  note: use brand colors
"""
    tasks = common.parse_todo_list(content)
    assert len(tasks) == 2
    assert tasks[0].description == "Add login form"
    assert tasks[0].unfinished
    assert tasks[1].done
    assert tasks[1].note == "use brand colors"

    rendered = common.render_todo_list(tasks)
    assert "[ ] Add login form" in rendered
    assert "note: use brand colors" in rendered


def test_parse_legacy_todo_list_with_type_tags():
    content = """# Todo

- [ ] [coding] Add login form
- [x] [presentation] Demo deck
"""
    tasks = common.parse_todo_list(content)
    assert len(tasks) == 2
    assert tasks[0].description == "Add login form"
    assert tasks[1].description == "Demo deck"


def test_mark_done_and_counts():
    tasks = [common.TodoTask("Fix bug")]
    assert common.unfinished_count(tasks) == 1
    mark_done(tasks, tasks[0])
    assert common.unfinished_count(tasks) == 0


def test_mark_subtask_complete_marks_one_line_in_full_plan(tmp_path):
    run_log = tmp_path / "run.json"
    content = """<!-- task_type: coding -->
# Plan: Auth

## Todo list
- [ ] Add login <!-- verify: pytest tests/test_login.py -v -->
- [ ] Add logout <!-- verify: pytest tests/test_logout.py -v -->
"""
    common.write_todo_list(content, run_log_path=run_log)
    result = plan_mod._mark_subtask_complete("Add login", run_log_path=run_log)
    assert "Marked complete: Add login" in result
    assert "Remaining unchecked: 1" in result
    assert "all_complete=false" in result
    updated = common.read_todo_list(run_log)
    assert "- [x] Add login" in updated
    assert "- [ ] Add logout" in updated
    assert "## Todo list" in updated


def test_mark_subtask_complete_all_complete(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list("# Todo\n\n- [ ] Ship feature\n", run_log_path=run_log)
    result = plan_mod._mark_subtask_complete("Ship feature", run_log_path=run_log)
    assert "all_complete=true" in result
    assert common.unfinished_count(common.load_tasks(run_log)) == 0


def test_complete_subtask_after_review_is_idempotent(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list(
        "# Todo\n\n## Todo list\n- [x] Create HTML slides\n",
        run_log_path=run_log,
    )
    result = plan_mod.complete_subtask_after_review("Create HTML slides", run_log_path=run_log)
    assert "already complete" in result.lower()


def test_mark_subtask_complete_no_match(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list("# Todo\n\n- [ ] Alpha\n", run_log_path=run_log)
    result = plan_mod._mark_subtask_complete("Missing item", run_log_path=run_log)
    assert "Tool error:" in result
    assert "Alpha" in result


def test_format_unfinished_todo_summary(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list(
        "# Todo\n\n- [x] Done\n- [ ] Alpha\n- [ ] Beta\n",
        run_log_path=run_log,
    )
    summary = format_unfinished_todo_summary(run_log)
    assert "Alpha" in summary
    assert "Beta" in summary
    assert "Done" not in summary


def test_task_type_marker_round_trip(tmp_path):
    run_log = tmp_path / "run.json"
    persist_task_type(run_log, "coding")
    assert common.read_task_type(run_log) == "coding"
    tasks = [common.TodoTask("Add login form")]
    save_tasks(tasks, run_log)
    assert common.read_task_type(run_log) == "coding"


def test_clear_plan_already_empty(tmp_path):
    run_log = tmp_path / "run.json"
    assert plan_mod.clear_plan(run_log_path=run_log) == "Todo list is already empty."


def test_clear_plan_removes_todo_list(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list(
        "<!-- task_type: coding -->\n# Plan: Auth\n\n- [ ] Add login\n- [ ] Add logout\n",
        run_log_path=run_log,
    )
    result = plan_mod.clear_plan(run_log_path=run_log)
    assert "Cleared todo_list" in result
    assert "Discarded 2 unchecked item(s)" in result
    assert "agent_planner" in result
    assert common.read_todo_list(run_log) == ""
    assert plan_mod.read_plan(run_log_path=run_log) == "Todo list is empty."


def test_read_plan_tool(tmp_path):
    run_log = tmp_path / "run.json"
    assert plan_mod.read_plan(run_log_path=run_log) == "Todo list is empty."
    common.write_todo_list("# Todo\n\n- [ ] Ship feature\n", run_log_path=run_log)
    assert "Ship feature" in plan_mod.read_plan(run_log_path=run_log)


def test_read_plan_lists_ready_branches(tmp_path):
    from langbridge_code.agents.common import worktree as worktree_mod

    run_log = tmp_path / "run.json"
    common.write_todo_list("# Todo\n\n- [ ] Ship feature\n", run_log_path=run_log)
    worktree_mod.record_branch(
        run_log,
        worktree_mod.WorktreeInfo("lb/run/t1-auth", tmp_path / "wt", "Add auth"),
        "ready",
    )
    plan = plan_mod.read_plan(run_log_path=run_log)
    assert "Ready feature branches" in plan
    assert "lb/run/t1-auth" in plan


def test_parse_todo_list_ignores_plan_sections():
    content = """# Plan: Auth

## Out of scope
- No OAuth provider changes

## Todo list
- [ ] Add login <!-- verify: pytest tests/test_login.py -v -->
- [x] Done step
"""
    tasks = common.parse_todo_list(content)
    assert len(tasks) == 2
    assert tasks[0].description.startswith("- [ ] Add login") or "Add login" in tasks[0].description
    assert task_verify_command(tasks[0]) == "pytest tests/test_login.py -v"


def test_task_verify_command_empty_when_missing():
    task = common.TodoTask(description="Ship feature")
    assert task_verify_command(task) == ""


def test_resolve_single_worker_task_accepts_one_todo(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list(
        "# Todo\n\n- [ ] Add login\n- [ ] Add logout\n",
        run_log_path=run_log,
    )
    canonical, error = common.resolve_single_worker_task(
        "Add login <!-- verify: pytest tests/test_login.py -v -->",
        run_log,
    )
    assert error is None
    assert canonical == "Add login"


def test_resolve_single_worker_task_keeps_todo_markers(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list(
        "# Todo\n\n- [ ] Add auth <!-- parallel paths:src/auth/** -->\n",
        run_log_path=run_log,
    )
    canonical, error = common.resolve_single_worker_task(
        "Add auth <!-- parallel paths:src/auth/** -->",
        run_log,
    )
    assert error is None
    assert "parallel paths" in canonical


def test_resolve_single_worker_task_rejects_multiple_todos(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list(
        "# Todo\n\n- [ ] Alpha\n- [ ] Beta\n- [ ] Gamma\n",
        run_log_path=run_log,
    )
    prompt = """Implement all:
- [ ] Alpha
- [ ] Beta
- [ ] Gamma
"""
    canonical, error = common.resolve_single_worker_task(prompt, run_log)
    assert canonical is None
    assert "multiple todo checkboxes" in error


def test_resolve_single_worker_task_rejects_bundled_descriptions(tmp_path):
    run_log = tmp_path / "run.json"
    common.write_todo_list(
        "# Todo\n\n- [ ] Alpha\n- [ ] Beta\n",
        run_log_path=run_log,
    )
    prompt = "Do Alpha and also Beta and ship both"
    canonical, error = common.resolve_single_worker_task(prompt, run_log)
    assert canonical is None
    assert "bundles 2 unfinished todos" in error


def test_agent_worker_rejects_multi_subtask_prompt(tmp_path, monkeypatch):
    from langbridge_code.tools.agent_worker_reviewer import build_agent_worker_tool

    run_log = tmp_path / "run.json"
    common.write_todo_list(
        "# Todo\n\n- [ ] Alpha\n- [ ] Beta\n",
        run_log_path=run_log,
    )
    dispatched = []

    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.dispatch_worker",
        lambda *args, **kwargs: dispatched.append(True),
    )

    agent_worker = build_agent_worker_tool(
        api_key="key",
        model="model",
        run_log_path=run_log,
        turn_id=1,
        messages=[],
        target="ship",
    )
    reply = agent_worker(
        prompt="- [ ] Alpha\n- [ ] Beta",
        description="worker",
    )

    assert not dispatched
    assert "Tool error:" in reply
    assert "exactly one" in reply.lower() or "multiple" in reply.lower()
