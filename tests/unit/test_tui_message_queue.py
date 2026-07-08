from langbridge_code.ui.message_queue import UserMessageQueue
from langbridge_code.ui.tui import LangBridgeTui


def _bare_tui():
    tui = LangBridgeTui.__new__(LangBridgeTui)
    tui.turn_active = False
    tui.pending_question = None
    tui.message_queue = UserMessageQueue()
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
    user_lines = []
    system_lines = []
    tui.write_user = lambda text, **kwargs: user_lines.append((text, kwargs))
    tui.write_system = lambda text, **kwargs: system_lines.append(text)
    tui.update_status = lambda: None
    tui._ensure_input_writable = lambda **kwargs: None
    tui.begin_turn = lambda text, **kwargs: user_lines.append(("begin", text, kwargs))
    return tui, user_lines, system_lines


def test_enqueue_while_busy_keeps_message_for_later():
    tui, user_lines, system_lines = _bare_tui()
    tui.turn_active = True

    tui.enqueue_user_message("also fix tests")

    assert len(tui.message_queue) == 1
    assert user_lines[0] == ("also fix tests", {"queued": True})
    assert any("Queued" in line for line in system_lines)


def test_drain_starts_next_turn_without_redisplaying_user():
    tui, user_lines, system_lines = _bare_tui()
    tui.message_queue.enqueue("next task")

    started = tui.drain_message_queue()

    assert started is True
    assert len(tui.message_queue) == 0
    assert user_lines == [("begin", "next task", {"show_user": False})]


def test_begin_turn_keeps_input_writable():
    tui, _, _ = _bare_tui()
    input_box = type("Box", (), {"disabled": True, "focused": False})()
    input_box.focus = lambda: setattr(input_box, "focused", True)
    tui._input_box = lambda: input_box
    tui.turn_snapshot = None
    tui.run_worker = lambda fn, **kwargs: None
    tui.begin_turn = LangBridgeTui.begin_turn.__get__(tui, LangBridgeTui)
    tui._ensure_input_writable = LangBridgeTui._ensure_input_writable.__get__(tui, LangBridgeTui)

    tui.begin_turn("work")

    assert input_box.disabled is False
    assert input_box.focused is True


def test_cmd_queue_clear():
    tui, _, system_lines = _bare_tui()
    tui.message_queue.enqueue("one")
    tui.message_queue.enqueue("two")

    tui.cmd_queue("clear")

    assert len(tui.message_queue) == 0
    assert any("Cleared 2" in line for line in system_lines)
