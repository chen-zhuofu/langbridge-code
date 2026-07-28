"""Per-task progress notes for subagents (worker/explorer)."""
from langbridge_code.agents.common.task_progress import TaskProgress
from langbridge_code.context.common.stack import ContextStack
from langbridge_code.util.artifacts import task_progress_path
from langbridge_code.util.progress import read_progress, write_progress_note
from langbridge_code.util.agent_traces import append_agent_raw_round, reserve_agent_trace


def test_task_progress_path_is_stable_per_task_name(tmp_path):
    first = task_progress_path(tmp_path, "task 3: game state")
    again = task_progress_path(tmp_path, "task 3: game state")
    other = task_progress_path(tmp_path, "task 4: UI wiring")
    assert first == again
    assert first != other
    assert first.name == "progress.md"
    assert first.parent.parent == tmp_path


def test_task_progress_path_requires_task_name(tmp_path):
    assert task_progress_path(tmp_path, "") is None
    assert task_progress_path(None, "task") is None


def test_write_note_goes_to_task_file_not_session_progress(tmp_path):
    write_progress_note(tmp_path, "did the thing", "task-3")
    task_file = task_progress_path(tmp_path, "task-3")
    assert task_file.is_file()
    assert "did the thing" in task_file.read_text(encoding="utf-8")
    assert not (tmp_path / "progress.md").exists()


def test_write_note_overrides_task_file(tmp_path):
    write_progress_note(tmp_path, "#### Work done\n- first", "task-3")
    write_progress_note(tmp_path, "#### Work done\n- second", "task-3")
    text = read_progress(tmp_path, "task-3")
    assert "second" in text
    assert "first" not in text


def _stack():
    return ContextStack(system_content="sys", label="Worker")


def test_attach_pins_existing_notes_as_progress_block(tmp_path):
    write_progress_note(tmp_path, "wrote WumpusGame.move", "task-3")
    progress = TaskProgress("key", "model", tmp_path, "task-3", label="Worker")
    stack = _stack()
    progress.attach(stack, [])
    assert "wrote WumpusGame.move" in (stack.progress_block or "")


def test_attach_includes_prior_agent_traces_when_redispatching(tmp_path, monkeypatch):
    write_progress_note(tmp_path, "Stopped while fixing PUT semantics", "task-3")
    path, _ = reserve_agent_trace(tmp_path, "Worker", "task-3")
    append_agent_raw_round(
        path,
        round_index=0,
        messages=[{"role": "assistant", "content": "still wiring handlers"}],
    )
    monkeypatch.setattr(
        "langbridge_code.util.agent_traces.build_agent_resume_background",
        lambda *a, **k: (
            "Stopped while fixing PUT semantics\n\n"
            "## Raw Worker traces\n\nstill wiring handlers"
        ),
    )
    progress = TaskProgress(
        "key",
        "kimi-k2.7-code",
        tmp_path,
        "task-3",
        label="Worker",
        current_trace=None,
    )
    stack = _stack()
    progress.attach(stack, [])
    assert "Stopped while fixing PUT semantics" in (stack.progress_block or "")
    assert "still wiring handlers" in (stack.progress_block or "")


def test_write_note_edits_file_without_touching_progress_block(tmp_path, monkeypatch):
    def fake_fork(*args, **kwargs):
        from langbridge_code.util.progress import write_progress

        write_progress(
            tmp_path,
            "# Session progress\n\n#### Work done\n_desc_\n\n- implemented move()\n",
            "task-3",
        )
        return "Noted in progress.md: implemented move()"

    monkeypatch.setattr(
        "langbridge_code.agents.common.fork.fork_progress_note",
        fake_fork,
    )
    progress = TaskProgress("key", "model", tmp_path, "task-3", label="Worker")
    stack = _stack()
    messages = [{"role": "system", "content": "sys"}]
    progress.attach(stack, messages)
    # Fresh attach with empty file → no progress block.
    assert stack.progress_block is None
    result = progress.write_note()
    assert "implemented move()" in read_progress(tmp_path, "task-3")
    assert "Noted" in result
    # Mid-turn write must NOT inject into active <progress>.
    assert stack.progress_block is None


def test_second_write_note_updates_disk_not_live_block(tmp_path, monkeypatch):
    notes = [
        "# Session progress\n\n#### Key discoveries\n_desc_\n\n- first fact\n",
        "# Session progress\n\n#### Key discoveries\n_desc_\n\n- first fact\n- second fact\n",
    ]
    calls = {"n": 0}

    def fake_fork(*args, **kwargs):
        from langbridge_code.util.progress import write_progress

        content = notes[calls["n"]]
        calls["n"] += 1
        write_progress(tmp_path, content, "task-3")
        return "Noted in progress.md: updated"

    monkeypatch.setattr(
        "langbridge_code.agents.common.fork.fork_progress_note", fake_fork
    )
    progress = TaskProgress("key", "model", tmp_path, "task-3", label="Worker")
    stack = _stack()
    messages = [{"role": "system", "content": "sys"}]
    progress.attach(stack, messages)
    progress.write_note()
    progress.write_note()
    text = read_progress(tmp_path, "task-3")
    assert "second fact" in text
    assert "first fact" in text  # Edit-style update keeps prior facts
    assert stack.progress_block is None


def test_maybe_force_write_after_silent_rounds(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.agents.common.task_progress.PROGRESS_NOTE_REMINDER_ROUNDS", 2
    )
    forced = {"n": 0}

    def fake_write(self, **_ignored):
        forced["n"] += 1
        self._rounds_since_note = 0
        return "Noted (forced)."

    monkeypatch.setattr(
        "langbridge_code.agents.common.task_progress.TaskProgress.write_note",
        fake_write,
    )
    progress = TaskProgress("key", "model", tmp_path, "task-3", label="Worker")
    stack = _stack()
    progress.attach(stack, [])
    progress.maybe_force_write()
    progress.maybe_force_write()
    assert forced["n"] == 0
    progress.maybe_force_write()
    assert forced["n"] == 1


def test_refresh_block_after_compaction_rereads_file(tmp_path):
    progress = TaskProgress("key", "model", tmp_path, "task-3", label="Worker")
    stack = _stack()
    progress.attach(stack, [])
    assert stack.progress_block is None
    write_progress_note(tmp_path, "new fact", "task-3")
    assert stack.on_compacted is not None
    stack.on_compacted(stack)
    assert "new fact" in (stack.progress_block or "")
