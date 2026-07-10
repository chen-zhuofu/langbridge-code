import json
import sys
import types

import pytest

from langbridge_code.tools.browser import browse_webpage


class _FakePage:
    url = "https://example.com/app"

    def goto(self, url, *, wait_until, timeout):
        assert url == "https://example.com/app"
        assert wait_until == "domcontentloaded"

    def wait_for_timeout(self, wait_ms):
        assert wait_ms == 500

    def title(self):
        return "Example App"

    def inner_text(self, selector):
        assert selector == "body"
        return "  Hello   JS world  "


class _FakeBrowser:
    def new_page(self):
        return _FakePage()

    def close(self):
        return None


class _FakeChromium:
    def launch(self, *, headless):
        assert headless is True
        return _FakeBrowser()


class _FakePlaywright:
    chromium = _FakeChromium()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _install_fake_playwright(monkeypatch):
    fake_api = types.SimpleNamespace(
        sync_playwright=lambda: _FakePlaywright(),
        Error=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_api)


def test_browse_webpage_renders_text(monkeypatch):
    _install_fake_playwright(monkeypatch)
    payload = json.loads(
        browse_webpage(
            "https://example.com/app",
            max_chars=1000,
            timeout_seconds=10,
            wait_after_load_ms=500,
        )
    )
    assert payload["title"] == "Example App"
    assert payload["final_url"] == "https://example.com/app"
    assert payload["engine"] == "playwright/chromium"
    assert payload["text"] == "Hello JS world"


def test_browse_webpage_requires_playwright(monkeypatch):
    import builtins

    real_import = builtins.__import__
    monkeypatch.delitem(sys.modules, "playwright.sync_api", raising=False)

    def selective_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "playwright.sync_api" or (
            fromlist and "sync_api" in fromlist and name == "playwright"
        ):
            raise ImportError("no playwright")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", selective_import)
    with pytest.raises(RuntimeError, match="Playwright browser is not ready"):
        browse_webpage("https://example.com")
