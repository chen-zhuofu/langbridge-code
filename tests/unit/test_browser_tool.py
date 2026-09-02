import threading

import pytest

from langbridge_code.tools import GOAL_VERIFICATION_TOOL_NAMES, MAIN_TOOL_NAMES
from langbridge_code.tools.browser import (
    TOOL_SCHEMAS,
    _BrowserState,
    _BrowserWorker,
    click_is_read_only,
    request_is_read_only,
    validate_browser_url,
)


class _ClosedPage:
    def is_closed(self):
        return True


class TargetClosedError(Exception):
    pass


def test_browser_is_one_main_only_tool_with_actions():
    assert [schema["name"] for schema in TOOL_SCHEMAS] == ["browser"]
    actions = TOOL_SCHEMAS[0]["parameters"]["properties"]["action"]["enum"]
    assert actions == ["open", "snapshot", "click", "scroll", "screenshot", "close"]
    assert "browser" in MAIN_TOOL_NAMES
    assert "browser" not in GOAL_VERIFICATION_TOOL_NAMES


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/home",
        "https://www.x.com/search?q=agents",
        "https://twitter.com/openai/status/1",
    ],
)
def test_browser_url_allows_x_only(url):
    assert validate_browser_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://x.com/home",
        "https://example.com/",
        "https://notx.com/",
        "https://x.com/compose/post",
        "https://x.com/messages",
    ],
)
def test_browser_url_blocks_external_and_write_surfaces(url):
    with pytest.raises(ValueError):
        validate_browser_url(url)


def test_browser_click_blocks_account_mutations():
    assert click_is_read_only(tag="a", role="", label="Open post") is True
    assert click_is_read_only(tag="button", role="button", label="Show more") is True
    assert click_is_read_only(tag="button", role="button", label="Like") is False
    assert click_is_read_only(tag="button", role="button", label="关注") is False


def test_browser_network_guard_blocks_known_social_mutations():
    assert request_is_read_only(method="GET", url="https://x.com/i/api/home") is True
    assert request_is_read_only(
        method="POST", url="https://x.com/i/api/graphql/abc/HomeTimeline"
    ) is True
    assert request_is_read_only(
        method="POST", url="https://x.com/i/api/graphql/abc/FavoriteTweet"
    ) is False


def test_route_handler_ignores_target_closed_race():
    class Request:
        method = "GET"
        url = "https://x.com/home"

    class Route:
        def continue_(self):
            raise TargetClosedError("Target page, context or browser has been closed")

    _BrowserState._route_request(Route(), Request())


def test_route_handler_does_not_hide_unrelated_errors():
    class Request:
        method = "GET"
        url = "https://x.com/home"

    class Route:
        def continue_(self):
            raise RuntimeError("routing broke")

    with pytest.raises(RuntimeError, match="routing broke"):
        _BrowserState._route_request(Route(), Request())


def test_closed_browser_is_cleaned_before_playwright_is_restarted(monkeypatch, tmp_path):
    events = []

    class OldContext:
        def unroute_all(self, *, behavior):
            events.append(f"old-context-unrouted-{behavior}")

        def close(self):
            events.append("old-context-closed")
            raise TargetClosedError("Target page, context or browser has been closed")

    class OldPlaywright:
        def stop(self):
            events.append("old-playwright-stopped")

    class NewContext:
        pages = [object()]

        def route(self, *_args):
            events.append("new-context-routed")

        def unroute_all(self, *, behavior):
            events.append(f"new-context-unrouted-{behavior}")

        def close(self):
            events.append("new-context-closed")

    class Chromium:
        def launch_persistent_context(self, *_args, **_kwargs):
            events.append("new-context-launched")
            return NewContext()

    class NewPlaywright:
        chromium = Chromium()

        def stop(self):
            events.append("new-playwright-stopped")

    class Manager:
        def start(self):
            events.append("new-playwright-started")
            return NewPlaywright()

    state = _BrowserState()
    state.context = OldContext()
    state.playwright = OldPlaywright()
    state.page = _ClosedPage()
    monkeypatch.setattr("langbridge_code.tools.browser.browser_profile_dir", lambda: tmp_path)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: Manager())

    page = state.ensure_page()

    assert page is state.page
    assert events[:3] == [
        "old-context-unrouted-ignoreErrors",
        "old-context-closed",
        "old-playwright-stopped",
    ]
    assert events[3:] == [
        "new-playwright-started",
        "new-context-launched",
        "new-context-routed",
    ]
    state.close()


def test_worker_stop_waits_for_state_cleanup(monkeypatch):
    cleaned = threading.Event()

    class State:
        def run(self, _action, _arguments):
            return {"status": "ok"}

        def close(self):
            cleaned.set()
            return {"status": "closed"}

    monkeypatch.setattr("langbridge_code.tools.browser._BrowserState", State)
    worker = _BrowserWorker()

    assert worker.call("snapshot", {}) == {"status": "ok"}
    worker.stop()

    assert cleaned.is_set()
    assert worker._thread is None
