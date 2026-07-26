"""Minimal OpenAI-compatible chat-completions server for TUI e2e tests.

Behavior is keyed off the last user message so scenarios can steer replies:
  contains "USE_TOOL"  -> first call returns a bash tool call (triggers approval),
                          second call returns a plain reply
  anything else        -> echo-style plain assistant reply
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_calls_by_convo: dict[str, int] = {}
_lock = threading.Lock()


_AUTOMATED_MARKERS = ("[CONTEXT_STATUS]", "<background>", "<memory>", "<progress>", "<skill_index>")


def _last_user_text(messages) -> str:
    """Last human user message, skipping engine-injected automated blocks."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(part.get("text", "")) for part in content)
        text = str(content)
        if any(marker in text[:200] for marker in _AUTOMATED_MARKERS):
            continue
        return text
    return ""


def _tool_call_response():
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "bash",
                                "arguments": json.dumps(
                                    {"description": "e2e approval test", "command": "echo approved-run"}
                                ),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _text_response(text: str):
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        user_text = _last_user_text(messages)

        has_tool_result = any(message.get("role") == "tool" for message in messages)
        if "USE_TOOL" in user_text and not has_tool_result:
            payload = _tool_call_response()
        elif "USE_TOOL" in user_text:
            payload = _text_response("tool finished; e2e reply after approval")
        elif "SLOW" in user_text:
            import time

            time.sleep(3)
            payload = _text_response("slow reply done")
        else:
            payload = _text_response(f"e2e-reply: {user_text[:60]}")

        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8901
    serve(port)
    print(f"fake LLM on 127.0.0.1:{port}", flush=True)
    threading.Event().wait()
