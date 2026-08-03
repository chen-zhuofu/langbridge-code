import json
from pathlib import Path

import httpx
import pytest

from langbridge_code.agents.common.workspace import (
    configure_agent_artifacts,
    set_workspace_root,
)
from langbridge_code.settings import MAX_WEBPAGE_CHARS
from langbridge_code.tools.execution import TOOL_OUTPUT_PREVIEW_CHARS
from langbridge_code.tools.filesystem import read_file
from langbridge_code.tools.web import read_webpage, truncate
from langbridge_code.util.trace_log import set_trace_context


@pytest.fixture
def isolated_workspace(tmp_path):
    set_workspace_root(tmp_path)
    yield tmp_path
    set_workspace_root(None)
    set_trace_context(None)
    configure_agent_artifacts(None, label="LangBridge")


class _FakeResponse:
    def __init__(self, text, *, url="https://example.com/doc", status_code=200, content_type="text/html"):
        self.text = text
        self.url = httpx.URL(url)
        self.status_code = status_code
        self.headers = {"content-type": content_type}


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        return self._response


def test_truncate_back_compat_still_cuts():
    text, truncated = truncate("abcdef", 3)
    assert truncated is True
    assert text.startswith("abc")
    assert "[truncated]" in text


def test_read_webpage_spills_oversized_body(isolated_workspace, monkeypatch):
    session = isolated_workspace / "session-web"
    session.mkdir()
    configure_agent_artifacts(session, label="LangBridge")

    body = "<html><title>Docs</title><body>" + ("paragraph " * 500) + "</body></html>"
    monkeypatch.setattr(
        "langbridge_code.tools.web.httpx.Client",
        lambda **kwargs: _FakeClient(_FakeResponse(body)),
    )

    result = json.loads(
        read_webpage(
            "https://example.com/doc",
            max_chars=80,
            run_log_path=session,
        )
    )
    assert result["truncated"] is True
    assert result["title"] == "Docs"
    path = Path(result["output_path"])
    assert path.is_file()
    assert path.parent == (session / "main" / "attachments").resolve()
    assert path.name.startswith("webpage-")
    full = path.read_text(encoding="utf-8")
    assert len(full) > 80
    assert result["text"].startswith(full[:TOOL_OUTPUT_PREVIEW_CHARS])
    assert str(path) in result["text"]
    assert "read_file" in result["text"]

    listed = read_file(str(path), offset=1, limit=3)
    assert "paragraph" in listed


def test_read_webpage_inline_when_under_limit(isolated_workspace, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.tools.web.httpx.Client",
        lambda **kwargs: _FakeClient(
            _FakeResponse("<html><title>Hi</title><body>short page</body></html>")
        ),
    )
    result = json.loads(read_webpage("https://example.com/short", max_chars=2000))
    assert result["truncated"] is False
    assert "output_path" not in result
    assert "short page" in result["text"]


def test_max_webpage_chars_matches_claude_web_fetch_budget():
    assert MAX_WEBPAGE_CHARS == 100_000
