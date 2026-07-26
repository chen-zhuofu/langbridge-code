"""Per-task progress notes for subagents (worker/explorer)."""
from langbridge_code.agents.common.task_progress import TaskProgress
from langbridge_code.context.common.stack import ContextStack
from langbridge_code.util.artifacts import task_progress_path
from langbridge_code.util.progress import (
    append_progress_note,
    last_progress_turn_id,
    read_progress,
)
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


def test_append_note_goes_to_task_file_not_session_progress(tmp_path):
    append_progress_note(tmp_path, 1, "did the thing", "task-3")
    task_file = task_progress_path(tmp_path, "task-3")
    assert task_file.is_file()
    assert "did the thing" in task_file.read_text(encoding="utf-8")
    assert not (tmp_path / "progress.md").exists()


def test_redispatch_opens_next_turn_section(tmp_path):
    append_progress_note(tmp_path, 1, "first dispatch work", "task-3")
    assert last_progress_turn_id(tmp_path, "task-3") == 1
    append_progress_note(tmp_path, 2, "second dispatch work", "task-3")
    text = read_progress(tmp_path, "task-3")
    assert "## Turn 1" in text
    assert "## Turn 2" in text


def _stack():
    return ContextStack(system_content="sys", label="Worker")


def test_attach_pins_existing_notes_as_progress_block(tmp_path):
    append_progress_note(tmp_path, 1, "wrote WumpusGame.move", "task-3")
    progress = TaskProgress("key", "model", tmp_path, "task-3", label="Worker")
    stack = _stack()
    progress.attach(stack, [])
    assert "wrote WumpusGame.move" in (stack.progress_block or "")
    assert progress.turn_id == 2


def test_attach_cold_resume_includes_prior_raw_trace(tmp_path):
    prior, _ = reserve_agent_trace(tmp_path, "Worker", "task-3")
    append_agent_raw_round(
        prior,
        round_index=0,
        messages=[{"role": "assistant", "content": "Stopped while fixing PUT semantics"}],
    )
    current, _ = reserve_agent_trace(tmp_path, "Worker", "task-3")
    progress = TaskProgress(
        "key",
        "kimi-k2.7-code",
        tmp_path,
        "task-3",
        label="Worker",
        current_trace=current,
    )
    stack = _stack()

    progress.attach(stack, [])

    assert "Stopped while fixing PUT semantics" in (stack.progress_block or "")


def test_attach_without_task_name_is_disabled(tmp_path):
    progress = TaskProgress("key", "model", tmp_path, "", label="Worker")
    assert not progress.enabled
    stack = _stack()
    progress.attach(stack, [])
    assert stack.progress_block is None


def test_write_note_appends_via_fork_and_updates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.agents.common.fork.fork_one_pass",
        lambda *args, **kwargs: "#### Work done\n- implemented move()",
    )
    monkeypatch.setattr(
        "langbridge_code.util.progress.maybe_compact_progress",
        lambda *args, **kwargs: False,
    )
    progress = TaskProgress("key", "model", tmp_path, "task-3", label="Worker")
    stack = _stack()
    progress.attach(stack, [{"role": "system", "content": "sys"}])
    result = progress.write_note()
    assert "implemented move()" in read_progress(tmp_path, "task-3")
    assert "Noted" in result


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
    append_progress_note(tmp_path, progress.turn_id, "new fact", "task-3")
    assert stack.on_compacted is not None
    stack.on_compacted(stack)
    assert "new fact" in (stack.progress_block or "")
