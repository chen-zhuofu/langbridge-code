"""Read-only Playwright browser control for the LangBridge main agent."""
from __future__ import annotations

import atexit
import fcntl
import json
import queue
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from langbridge_code.tools.common.description import DESCRIPTION_PARAMETER
from langbridge_code.util.app_paths import app_support_dir

DEFAULT_URL = "https://x.com/home"
MAX_SNAPSHOT_CHARS = 24_000
MAX_INTERACTIVE_ELEMENTS = 160
_ACTION_TIMEOUT_SECONDS = 45
_SHUTDOWN_TIMEOUT_SECONDS = _ACTION_TIMEOUT_SECONDS + 5
_ALLOWED_HOST_SUFFIXES = ("x.com", "twitter.com")
_BLOCKED_PATH_PREFIXES = ("/compose", "/messages")
_BLOCKED_BUTTON_RE = re.compile(
    r"\b(like|unlike|reply|repost|undo repost|follow|unfollow|bookmark|"
    r"remove bookmark|message|send|subscribe|post|mute|unmute|block|unblock|"
    r"report|delete|pin|not interested)\b|"
    r"(点赞|取消点赞|回复|转发|关注|取消关注|书签|私信|发送|发布|静音|"
    r"取消静音|屏蔽|取消屏蔽|举报|删除|置顶|不感兴趣)",
    re.IGNORECASE,
)
_BLOCKED_REQUEST_RE = re.compile(
    r"(CreateTweet|DeleteTweet|FavoriteTweet|UnfavoriteTweet|CreateRetweet|"
    r"DeleteRetweet|CreateBookmark|DeleteBookmark|CreateFollow|DestroyFriendship|"
    r"MuteUser|UnmuteUser|BlockUser|UnblockUser|CreateDM|SendMessage|"
    r"favorites/(create|destroy)|friendships/(create|destroy)|statuses/update|"
    r"direct_messages/new)",
    re.IGNORECASE,
)


def browser_profile_dir() -> Path:
    return app_support_dir() / "Browser" / "Profile"


def browser_screenshot_path() -> Path:
    return app_support_dir() / "Browser" / "Screenshots" / "latest.png"


def validate_browser_url(url: str) -> str:
    raw = (url or DEFAULT_URL).strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in _ALLOWED_HOST_SUFFIXES
    ):
        raise ValueError("Browser navigation is limited to HTTPS pages on x.com.")
    path = parsed.path.lower().rstrip("/")
    if any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in _BLOCKED_PATH_PREFIXES
    ):
        raise ValueError(
            "Posting and messaging surfaces are disabled in read-only Browser Use."
        )
    return raw


def click_is_read_only(*, tag: str, role: str, label: str) -> bool:
    if (tag or "").lower() not in {"button", "input", "textarea"} and (
        role or ""
    ).lower() != "button":
        return True
    return _BLOCKED_BUTTON_RE.search(label or "") is None


def request_is_read_only(*, method: str, url: str) -> bool:
    if (method or "GET").upper() in {"GET", "HEAD", "OPTIONS"}:
        return True
    return _BLOCKED_REQUEST_RE.search(url or "") is None


def _is_already_closed_error(error: Exception) -> bool:
    return error.__class__.__name__ == "TargetClosedError" or (
        "has been closed" in str(error).lower()
    )


class _BrowserState:
    def __init__(self) -> None:
        self.playwright = None
        self.context = None
        self.page = None
        self.profile_lock = None
        self.references: dict[int, object] = {}
        self.reference_metadata: dict[int, dict] = {}

    def ensure_page(self):
        if self.context is not None and self.page is not None:
            try:
                if not self.page.is_closed():
                    return self.page
            except Exception:  # noqa: BLE001
                # A browser closed outside LangBridge can make even the health
                # check fail. Treat that connection as stale and rebuild it.
                pass
        if (
            self.playwright is not None
            or self.context is not None
            or self.page is not None
        ):
            self.close()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright is not installed. Run `uv sync`, then restart LangBridge."
            ) from error

        profile = browser_profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        lock_path = profile.parent / "browser.lock"
        lock_handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lock_handle.close()
            raise RuntimeError(
                "The persistent X browser is in use by another LangBridge task. "
                "Wait for that task to finish or close its browser, then retry."
            ) from error
        self.profile_lock = lock_handle
        try:
            self.playwright = sync_playwright().start()
            self.context = self.playwright.chromium.launch_persistent_context(
                str(profile),
                headless=False,
                no_viewport=True,
            )
            self.context.route("**/*", self._route_request)
        except Exception:
            try:
                self.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self.page

    @staticmethod
    def _route_request(route, request) -> None:
        try:
            if request_is_read_only(method=request.method, url=request.url):
                route.continue_()
            else:
                route.abort("blockedbyclient")
        except Exception as error:  # noqa: BLE001
            # Closing Chromium can race with an in-flight route callback. The
            # request no longer matters once its page/context has disappeared.
            if not _is_already_closed_error(error):
                raise

    def close(self) -> dict:
        context, self.context = self.context, None
        playwright, self.playwright = self.playwright, None
        self.page = None
        self.references = {}
        self.reference_metadata = {}
        cleanup_error = None
        try:
            if context is not None:
                try:
                    context.unroute_all(behavior="ignoreErrors")
                except Exception as error:  # noqa: BLE001
                    if not _is_already_closed_error(error):
                        cleanup_error = error
                try:
                    context.close()
                except Exception as error:  # noqa: BLE001
                    if cleanup_error is None and not _is_already_closed_error(error):
                        cleanup_error = error
        finally:
            try:
                if playwright is not None:
                    playwright.stop()
            except Exception as error:  # noqa: BLE001
                if cleanup_error is None and not _is_already_closed_error(error):
                    cleanup_error = error
            finally:
                self._release_profile_lock()
        if cleanup_error is not None:
            raise cleanup_error
        return {"status": "closed"}

    def _release_profile_lock(self) -> None:
        if self.profile_lock is None:
            return
        fcntl.flock(self.profile_lock.fileno(), fcntl.LOCK_UN)
        self.profile_lock.close()
        self.profile_lock = None

    def run(self, action: str, arguments: dict) -> dict:
        if action == "close":
            return self.close()
        page = self.ensure_page()
        if action == "open":
            url = validate_browser_url(str(arguments.get("url") or DEFAULT_URL))
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2_500)
            page.bring_to_front()
            return self.snapshot()
        if action == "snapshot":
            return self.snapshot()
        if action == "scroll":
            direction = str(arguments.get("direction") or "down").lower()
            if direction not in {"up", "down"}:
                raise ValueError("direction must be 'up' or 'down'.")
            amount = max(100, min(int(arguments.get("amount") or 800), 4_000))
            page.mouse.wheel(0, amount if direction == "down" else -amount)
            page.wait_for_timeout(500)
            return self.snapshot()
        if action == "click":
            return self.click(int(arguments.get("ref") or 0))
        if action == "screenshot":
            path = browser_screenshot_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(path),
                full_page=bool(arguments.get("full_page", False)),
            )
            return {
                "url": page.url,
                "title": page.title(),
                "screenshot_path": str(path),
                "image_paths": [str(path)],
            }
        raise ValueError(f"Unknown browser action: {action}")

    def click(self, reference: int) -> dict:
        locator = self.references.get(reference)
        metadata = self.reference_metadata.get(reference)
        if locator is None or metadata is None:
            raise ValueError("Unknown or stale ref. Run browser(action='snapshot') again.")
        if not click_is_read_only(
            tag=str(metadata.get("tag") or ""),
            role=str(metadata.get("role") or ""),
            label=str(metadata.get("label") or ""),
        ):
            raise PermissionError(
                "That control can change X account state and is disabled in read-only Browser Use."
            )
        previous_pages = set(self.context.pages)
        locator.click(timeout=10_000)
        self.page.wait_for_timeout(700)
        new_pages = [page for page in self.context.pages if page not in previous_pages]
        if new_pages:
            candidate = new_pages[-1]
            try:
                validate_browser_url(candidate.url)
            except ValueError:
                candidate.close()
                raise PermissionError("Browser Use blocked a link outside x.com.") from None
            self.page = candidate
        try:
            validate_browser_url(self.page.url)
        except ValueError:
            self.page.go_back(wait_until="domcontentloaded", timeout=15_000)
            raise PermissionError("Browser Use blocked navigation outside x.com.") from None
        return self.snapshot()

    def snapshot(self) -> dict:
        page = self.page
        validate_browser_url(page.url)
        body = page.locator("body").inner_text(timeout=10_000)
        articles = []
        article_locator = page.locator("article:visible")
        for index in range(min(article_locator.count(), 40)):
            text = article_locator.nth(index).inner_text(timeout=3_000).strip()
            if text:
                articles.append(text[:3_000])

        self.references = {}
        self.reference_metadata = {}
        elements = []
        interactive = page.locator(
            "a:visible, button:visible, input:visible, textarea:visible, [role='button']:visible"
        )
        for index in range(min(interactive.count(), MAX_INTERACTIVE_ELEMENTS)):
            locator = interactive.nth(index)
            try:
                metadata = locator.evaluate(
                    """element => ({
                        tag: element.tagName.toLowerCase(),
                        role: element.getAttribute('role') || '',
                        label: element.getAttribute('aria-label') ||
                               element.innerText || element.getAttribute('placeholder') || '',
                        href: element.href || ''
                    })"""
                )
            except Exception:
                continue
            label = " ".join(str(metadata.get("label") or "").split())[:240]
            href = str(metadata.get("href") or "")
            if not label and not href:
                continue
            ref = len(elements) + 1
            normalized = {
                "ref": ref,
                "tag": str(metadata.get("tag") or ""),
                "role": str(metadata.get("role") or ""),
                "label": label,
                "href": href,
            }
            self.references[ref] = locator
            self.reference_metadata[ref] = normalized
            elements.append(normalized)

        return {
            "url": page.url,
            "title": page.title(),
            "articles": articles,
            "visible_text": body[:MAX_SNAPSHOT_CHARS],
            "elements": elements,
            "truncated": len(body) > MAX_SNAPSHOT_CHARS,
        }


class _BrowserWorker:
    def __init__(self) -> None:
        self._requests: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopping = False

    def _ensure_started_locked(self) -> None:
        if self._stopping:
            raise RuntimeError("Browser is shutting down.")
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._serve,
            name="langbridge-playwright",
            daemon=True,
        )
        self._thread.start()

    def call(self, action: str, arguments: dict) -> dict:
        response: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._ensure_started_locked()
            self._requests.put((action, arguments, response))
        try:
            ok, result = response.get(timeout=_ACTION_TIMEOUT_SECONDS)
        except queue.Empty as error:
            raise TimeoutError(
                f"Browser action timed out after {_ACTION_TIMEOUT_SECONDS} seconds."
            ) from error
        if ok:
            return result
        raise result

    def stop(self) -> None:
        response: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive() or self._stopping:
                return
            self._stopping = True
            self._requests.put(("__stop__", {}, response))
        try:
            response.get(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        except queue.Empty:
            return
        thread.join(timeout=1)
        with self._lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None
                self._stopping = False

    def _serve(self) -> None:
        state = _BrowserState()
        while True:
            action, arguments, response = self._requests.get()
            if action == "__stop__":
                try:
                    state.close()
                    response.put((True, None))
                except Exception as error:  # noqa: BLE001
                    response.put((False, error))
                return
            try:
                response.put((True, state.run(action, arguments)))
            except Exception as error:  # noqa: BLE001
                response.put((False, error))


_WORKER = _BrowserWorker()
atexit.register(_WORKER.stop)


def shutdown_browser() -> None:
    """Drain the Playwright worker before the hosting process exits."""
    _WORKER.stop()


def browser(
    action: str,
    url: str | None = None,
    ref: int | None = None,
    direction: str = "down",
    amount: int = 800,
    full_page: bool = False,
) -> str:
    selected = (action or "").strip().lower()
    allowed = {"open", "snapshot", "click", "scroll", "screenshot", "close"}
    if selected not in allowed:
        raise ValueError(f"action must be one of: {', '.join(sorted(allowed))}.")
    if selected == "click" and not ref:
        raise ValueError("ref is required for action='click'.")
    result = _WORKER.call(
        selected,
        {
            "url": url,
            "ref": ref,
            "direction": direction,
            "amount": amount,
            "full_page": full_page,
        },
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "browser",
        "description": (
            "Control a visible, persistent Playwright browser for read-only browsing on x.com. "
            "Use action=open to navigate, snapshot to read the page and refresh element refs, "
            "click to activate a safe ref, scroll to move the timeline, screenshot for visual "
            "inspection, and close when finished. Posting, replies, likes, follows, bookmarks, "
            "messages, and navigation outside X are blocked. The user performs login and any "
            "verification manually in the browser window."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": DESCRIPTION_PARAMETER,
                "action": {
                    "type": "string",
                    "enum": ["open", "snapshot", "click", "scroll", "screenshot", "close"],
                    "description": "Browser operation to perform.",
                },
                "url": {
                    "type": "string",
                    "description": "HTTPS x.com URL for action=open. Defaults to https://x.com/home.",
                },
                "ref": {
                    "type": "integer",
                    "description": "Element ref from the latest snapshot for action=click.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction for action=scroll.",
                    "default": "down",
                },
                "amount": {
                    "type": "integer",
                    "description": "Scroll distance in pixels, from 100 to 4000.",
                    "default": 800,
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture the whole page for action=screenshot.",
                    "default": False,
                },
            },
            "required": ["description", "action"],
            "additionalProperties": False,
        },
    }
]

TOOLS = {"browser": browser}
