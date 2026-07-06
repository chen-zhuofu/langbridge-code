from langbridge_code.workflow import todo as todo_mod


def test_parse_and_render_todo_list():
    content = """# Todo

- [ ] [coding] Add login form
- [x] [presentation] Demo deck
  note: use brand colors
"""
    tasks = todo_mod.parse_todo_list(content)
    assert len(tasks) == 2
    assert tasks[0].description == "Add login form"
    assert tasks[0].task_type == "coding"
    assert tasks[0].unfinished
    assert tasks[1].done
    assert tasks[1].note == "use brand colors"

    rendered = todo_mod.render_todo_list(tasks)
    assert "[ ] [coding] Add login form" in rendered
    assert "note: use brand colors" in rendered


def test_single_task_and_counts():
    tasks = todo_mod.single_task("Fix bug", task_type="coding")
    assert todo_mod.unfinished_count(tasks) == 1
    assert todo_mod.first_unfinished(tasks).description == "Fix bug"
    todo_mod.mark_done(tasks, tasks[0])
    assert todo_mod.unfinished_count(tasks) == 0
