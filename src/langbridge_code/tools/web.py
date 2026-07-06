import json
import re
from html.parser import HTMLParser

import httpx

from langbridge_cli.settings import (
    DEFAULT_WEB_TIMEOUT_SECONDS,
    MAX_WEB_TIMEOUT_SECONDS,
    MAX_WEBPAGE_CHARS,
)

USER_AGENT = "langbridge-cli/0.1 (+webpage reader)"

# Tags whose text content is markup/scripts, not readable page content.
_SKIP_TAGS = {"script", "style", "noscript", "template"}
# Block-level tags that should produce a line break in the extracted text.
_BLOCK_TAGS = {
    "p", "br", "div", "section", "article", "header", "footer", "nav",
    "ul", "ol", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre",
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "read_webpage",
        "description": (
            "Fetch a web page over HTTP(S) and return its readable text content "
            "(HTML stripped to plain text). Use it to read documentation, issues, "
            "or articles. Returns JSON with the page title and text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute http or https URL of the page to read.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of text to return before truncating.",
                    "default": MAX_WEBPAGE_CHARS,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Maximum time to wait for the request.",
                    "default": DEFAULT_WEB_TIMEOUT_SECONDS,
                },
            },
            "required": ["url"],
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


@tool("read_webpage")
def read_webpage(url, max_chars=MAX_WEBPAGE_CHARS, timeout_seconds=DEFAULT_WEB_TIMEOUT_SECONDS):
    url = validate_url(url)
    limit = max(1, int(max_chars))
    timeout = max(1, min(int(timeout_seconds), MAX_WEB_TIMEOUT_SECONDS))

    with httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = client.get(url)

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    title = ""
    if is_text_like(content_type):
        title, body = html_to_text(response.text) if "html" in content_type or not content_type else ("", response.text)
        body = collapse_whitespace(body)
    else:
        body = f"[non-text content: {content_type or 'unknown'}; cannot read as a web page]"

    text, truncated = truncate(body, limit)
    return json.dumps(
        {
            "url": url,
            "final_url": str(response.url),
            "status_code": response.status_code,
            "content_type": content_type,
            "title": title,
            "truncated": truncated,
            "text": text,
        },
        ensure_ascii=False,
        indent=2,
    )


def validate_url(url):
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError("url must start with http:// or https://")
    return url


def is_text_like(content_type):
    if not content_type:
        return True
    return any(token in content_type for token in ("html", "text", "json", "xml"))


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title_parts = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.parts.append(data)


def html_to_text(html):
    parser = _TextExtractor()
    parser.feed(html)
    title = collapse_whitespace("".join(parser.title_parts)).replace("\n", " ").strip()
    return title, "".join(parser.parts)


def collapse_whitespace(text):
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def truncate(text, limit):
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n\n[truncated]", True
