from unittest.mock import PropertyMock, patch

from langbridge_code.ui.tui import ChatLog, _tui_mouse_enabled


def test_chat_log_append_respects_scroll_position():
    log = ChatLog.__new__(ChatLog)
    written = {}

    def fake_write(content, scroll_end=None, **kwargs):
        written["content"] = content
        written["scroll_end"] = scroll_end

    log.write = fake_write
    with patch.object(type(log), "is_vertical_scroll_end", new_callable=PropertyMock) as scroll_end:
        scroll_end.return_value = False
        log.append("stay")
        assert written["scroll_end"] is False

        scroll_end.return_value = True
        log.append("follow")
        assert written["scroll_end"] is True


def test_tui_mouse_enabled_by_default(monkeypatch):
    monkeypatch.delenv("LANGBRIDGE_TUI_MOUSE", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    assert _tui_mouse_enabled() is True


def test_tui_mouse_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_TUI_MOUSE", "off")
    assert _tui_mouse_enabled() is False
