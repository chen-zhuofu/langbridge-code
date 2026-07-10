from langbridge_code.ui.terminal_sanitize import (
    is_terminal_control_only,
    strip_terminal_control_text,
)


def test_strip_mouse_sgr_sequences():
    raw = "hello [<35;223;12M[<35;216;15M world"
    assert strip_terminal_control_text(raw) == "hello  world"


def test_strip_csi_mouse_sequence():
    raw = "x\x1b[<0;159;15My"
    assert strip_terminal_control_text(raw) == "xy"


def test_strip_sudo_prompt_echo():
    raw = "Sorry\n[sudo] password for seanlinux:\n"
    assert strip_terminal_control_text(raw) == "Sorry\n"


def test_is_terminal_control_only():
    assert is_terminal_control_only("[<35;223;12M") is True
    assert is_terminal_control_only("hello") is False
