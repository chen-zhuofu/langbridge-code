from pathlib import Path

import anyio
import pytest
from textual.actions import SkipAction

from langbridge_code.llm.trace import ThoughtEvent
from langbridge_code.ui.tui import LangBridgeTui, SessionPicker, is_input_area


class _Widget:
    def __init__(self, widget_id=None, parent=None):
        self.id = widget_id
        self.parent = parent


def test_is_input_area_detects_composer_descendants():
    row = _Widget("input_row")
    gutter = _Widget("prompt_gutter", parent=row)
    box = _Widget("input", parent=row)
    assert is_input_area(box) is True
    assert is_input_area(gutter) is True
    assert is_input_area(row) is True
    assert is_input_area(_Widget("chat_log")) is False


def _bare_tui():
    tui = LangBridgeTui.__new__(LangBridgeTui)
    tui.turn_active = False
    tui.pending_question = None
    tui.pending_approval = None
    tui.message_queue = __import__(
        "langbridge_code.ui.message_queue", fromlist=["UserMessageQueue"]
    ).UserMessageQueue()
    tui.workflow_step = ""
    tui.streaming_phase = "idle"
    tui.state = "ready"
    tui.messages = []
    tui.model = "test-model"
    tui.cwd_display = "~"
    tui.git_branch = ""
    tui.session_goal = None
    tui.always_approve = False
    tui.run_log_path = None
    tui.turn_id = 0
    tui._active_turn_id = None
    tui._active_turn_user = ""
    tui.turn_snapshot = None
    input_box = type("Box", (), {"text": "", "disabled": False})()
    input_box.focus = lambda: None
    tui._input_box = lambda: input_box
    tui.clear_thinking = lambda: None
    tui._sync_streaming_phase = lambda: None
    tui.update_status = lambda: None
    tui._ensure_input_writable = lambda **kwargs: None
    user_lines = []
    tui.write_user = lambda text, **kwargs: user_lines.append((text, kwargs))
    tui.write_system = lambda text, **kwargs: None
    tui.begin_turn = lambda text, **kwargs: user_lines.append(("begin", text))
    return tui, input_box, user_lines


def test_submit_starts_turn_when_idle():
    tui, input_box, user_lines = _bare_tui()
    input_box.text = "继续"

    tui.submit_current_input()

    assert input_box.text == ""
    assert ("begin", "继续") in user_lines


def test_submit_recovers_stuck_turn_active_flag():
    tui, input_box, user_lines = _bare_tui()
    tui.turn_active = True
    tui._active_turn_id = None
    input_box.text = "继续"

    tui.submit_current_input()

    assert tui.turn_active is False
    assert ("begin", "继续") in user_lines


def test_release_composer_after_worker_unlocks_input():
    tui, _, _ = _bare_tui()
    tui.turn_active = True
    tui.state = "working"
    tui.workflow_step = "coding"
    focused = []
    tui._ensure_input_writable = lambda **kwargs: focused.append(kwargs.get("focus"))

    tui._release_composer_after_worker()

    assert tui.turn_active is False
    assert tui.state == "ready"
    assert tui.workflow_step == ""
    assert focused == [True]


def test_stale_trace_events_ignored_after_turn_ends():
    tui, _, _ = _bare_tui()
    tui.turn_active = False
    tui.state = "ready"
    tui.update_stream_preview = lambda event: (_ for _ in ()).throw(
        AssertionError("stale stream should be ignored")
    )
    tui.append_trace_line = lambda event: (_ for _ in ()).throw(
        AssertionError("stale trace should be ignored")
    )

    tui.add_trace_event(ThoughtEvent(role="LangBridge", kind="reasoning_stream", text="late"))

    assert tui.state == "ready"


def test_submit_input_yields_to_modal_screens(monkeypatch):
    tui, input_box, user_lines = _bare_tui()
    input_box.text = "ignored"
    monkeypatch.setattr(
        type(tui),
        "screen",
        property(lambda self: type("Modal", (), {"is_modal": True})()),
    )

    with pytest.raises(SkipAction):
        tui.action_submit_input()

    assert input_box.text == "ignored"
    assert user_lines == []


def test_session_picker_confirm_pick_dismisses_highlighted(tmp_path):
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")
    picker = SessionPicker.__new__(SessionPicker)
    picker.sessions = [first, second]
    dismissed = []
    picker.dismiss = lambda value: dismissed.append(value)
    option_list = type("List", (), {"highlighted": 1})()
    picker.query_one = lambda selector, kind: option_list

    picker.action_confirm_pick()

    assert dismissed == [second]


def test_submit_input_yields_when_composer_disabled(monkeypatch):
    tui, input_box, user_lines = _bare_tui()
    input_box.text = "ignored"
    input_box.disabled = True
    monkeypatch.setattr(
        type(tui),
        "screen",
        property(lambda self: type("Screen", (), {"is_modal": False})()),
    )

    with pytest.raises(SkipAction):
        tui.action_submit_input()

    assert input_box.text == "ignored"
    assert user_lines == []
