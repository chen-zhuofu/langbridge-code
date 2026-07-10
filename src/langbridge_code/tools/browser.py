"""Headless browser tool (Playwright) for JS-rendered web pages."""
from __future__ import annotations

import json

from langbridge_code.settings import (
    BROWSER_MAX_CONTENT_CHARS,
    BROWSER_MAX_TIMEOUT_SECONDS,
    BROWSER_DEFAULT_TIMEOUT_SECONDS,
    BROWSER_WAIT_AFTER_LOAD_MS,
)
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER
from langbridge_code.tools.web import collapse_whitespace, truncate, validate_url

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "browse_webpage",
        "description": (
            "Open a URL in a headless Chromium browser (Playwright — like Puppeteer) "
            "and return rendered page text after JavaScript runs. Use for SPAs, "
            "documentation sites that require JS, or when read_webpage returns empty "
            "or incomplete content. Returns JSON with title, final URL, and body text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "url": {
                    "type": "string",
                    "description": "Absolute http or https URL to open.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of rendered text to return.",
                    "default": BROWSER_MAX_CONTENT_CHARS,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Navigation timeout in seconds.",
                    "default": BROWSER_DEFAULT_TIMEOUT_SECONDS,
                },
                "wait_after_load_ms": {
                    "type": "integer",
                    "description": "Extra milliseconds to wait after load for late JS.",
                    "default": BROWSER_WAIT_AFTER_LOAD_MS,
                },
            },
            "required": ["purpose", "url"],
            "additionalProperties": False,
        },
    }
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


def _playwright_missing_message() -> str:
    return (
        "Playwright browser is not ready. Run once after install:\n"
        "  playwright install chromium\n"
        "  playwright install-deps chromium   # Linux system libraries; needs sudo"
    )


@tool("browse_webpage")
def browse_webpage(
    url,
    max_chars=BROWSER_MAX_CONTENT_CHARS,
    timeout_seconds=BROWSER_DEFAULT_TIMEOUT_SECONDS,
    wait_after_load_ms=BROWSER_WAIT_AFTER_LOAD_MS,
):
    url = validate_url(url)
    limit = max(1, int(max_chars))
    timeout_ms = max(1000, min(int(timeout_seconds), BROWSER_MAX_TIMEOUT_SECONDS) * 1000)
    wait_ms = max(0, min(int(wait_after_load_ms), 30_000))

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(_playwright_missing_message()) from error

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                final_url = page.url
                title = page.title()
                body = collapse_whitespace(page.inner_text("body"))
            finally:
                browser.close()
    except PlaywrightError as error:
        message = str(error).strip()
        if (
            "Executable doesn't exist" in message
            or "browserType.launch" in message
            or "shared libraries" in message
        ):
            raise RuntimeError(_playwright_missing_message()) from error
        raise

    text, truncated = truncate(body, limit)
    return json.dumps(
        {
            "url": url,
            "final_url": final_url,
            "title": title,
            "engine": "playwright/chromium",
            "truncated": truncated,
            "text": text,
        },
        ensure_ascii=False,
        indent=2,
    )
