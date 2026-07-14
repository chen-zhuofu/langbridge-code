from pathlib import Path

import anyio

from langbridge_code.ui.tui import LangBridgeTui


def test_session_picker_enter_resumes_selection(tmp_path, monkeypatch):
    sessions = []
    for name in ("one", "two"):
        path = tmp_path / f"session-{name}"
        path.mkdir()
        (path / "progress.md").write_text("# Session progress\n", encoding="utf-8")
        sessions.append(path)

    monkeypatch.setattr("langbridge_code.ui.tui.list_session_logs", lambda: list(sessions))

    async def run():
        app = LangBridgeTui(api_key="test-key", model="test-model")
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert type(app.screen).__name__ == "SessionPicker"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert type(app.screen).__name__ != "SessionPicker"

    anyio.run(run)
