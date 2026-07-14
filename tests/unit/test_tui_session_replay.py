from langbridge_code.ui.tui import LangBridgeTui


def test_replay_progress_shows_last_turn_stub():
    tui = LangBridgeTui.__new__(LangBridgeTui)
    lines = []

    tui.write_system = lambda text, **kwargs: lines.append(("system", text))

    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session-demo"
        path.mkdir()
        (path / "progress.md").write_text(
            "# Session progress\n\n"
            "## Turn 1\n\n**In:** hello\n\n**Out:** hi\n\n"
            "## Turn 2\n\n**In:** build game\n\n**Out:** working\n",
            encoding="utf-8",
        )
        tui._replay_progress(path)

    assert len(lines) == 1
    assert lines[0][0] == "system"
    assert "## Turn 2" in lines[0][1]
    assert "build game" in lines[0][1]
    assert "## Turn 1" not in lines[0][1]
