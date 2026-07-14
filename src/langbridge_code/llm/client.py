"""LLM client — OpenAI Responses API or Moonshot/Kimi chat completions."""
import json
import time
import uuid

from openai import OpenAI, OpenAIError, RateLimitError

from langbridge_code.llm.debug import print_llm_request, print_llm_response


class ApiQuotaExceeded(RuntimeError):
    """Non-retryable provider quota limit (e.g. Moonshot organization TPD)."""


def rate_limit_is_non_retryable(error: RateLimitError) -> bool:
    """Return True when backing off will not help until quota resets."""
    text = str(error).lower()
    return (
        "tpd" in text
        or "tokens per day" in text
        or "rate_limit_reached_error" in text and "daily" in text
    )


def quota_exceeded_message(error: RateLimitError) -> str:
    return (
        "API daily token quota is exhausted (provider TPD limit). "
        "Wait for the daily reset, or switch provider/model/API key in ~/.langbridge/config.json."
    )


def format_api_error(error: BaseException) -> str:
    if isinstance(error, ApiQuotaExceeded):
        return str(error)
    if isinstance(error, RateLimitError) and rate_limit_is_non_retryable(error):
        return quota_exceeded_message(error)
    text = str(error).strip()
    if "429" in text and "tpd" in text.lower():
        return quota_exceeded_message(RateLimitError(text))
    if len(text) > 400:
        return f"Request failed: {text[:400]}…"
    return f"Request failed: {text}"
from langbridge_code.llm.parse import extract_output_text
from langbridge_code.settings import (
    API_BASE_URL,
    API_MAX_RETRIES,
    API_PROVIDER,
    API_STREAMING_ENABLED,
    API_TIMEOUT_SECONDS,
    load_config,
)

_STREAM_EMIT_INTERVAL_SECONDS = 0.08


def make_client(api_key):
    kwargs = {
        "api_key": api_key,
        "timeout": API_TIMEOUT_SECONDS,
        "max_retries": API_MAX_RETRIES,
    }
    if API_BASE_URL:
        kwargs["base_url"] = API_BASE_URL
    return OpenAI(**kwargs)


def uses_responses_api(provider=None):
    return (provider or API_PROVIDER) == "openai"


def to_chat_tools(tool_schemas):
    tools = []
    for schema in tool_schemas or []:
        tools.append({
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return tools


def _reasoning_text(item) -> str:
    parts = []
    for part in item.get("summary") or []:
        if isinstance(part, dict) and part.get("type") == "summary_text":
            text = part.get("text")
            if text:
                parts.append(str(text))
    if parts:
        return "".join(parts)
    content = item.get("content")
    return str(content) if content else ""


def to_chat_messages(agent_input):
    """Convert internal agent items to OpenAI-compatible chat messages.

    Preserves Kimi/Moonshot ``reasoning_content`` on assistant turns so
    thinking models (kimi-k2.7-code, kimi-k2.6 with keep=all) keep continuity
    across multi-step tool calls.
    """
    messages = []
    pending_calls = []
    pending_reasoning = None

    def flush_assistant(*, content=None):
        nonlocal pending_calls, pending_reasoning
        if not pending_calls and content is None and not pending_reasoning:
            return
        message = {
            "role": "assistant",
            "content": content,
        }
        if pending_reasoning:
            message["reasoning_content"] = pending_reasoning
        if pending_calls:
            message["tool_calls"] = [
                {
                    "id": call["call_id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call.get("arguments") or "{}",
                    },
                }
                for call in pending_calls
            ]
        messages.append(message)
        pending_calls = []
        pending_reasoning = None

    for item in agent_input:
        role = item.get("role")
        item_type = item.get("type")

        if item_type == "reasoning":
            text = _reasoning_text(item)
            if text:
                pending_reasoning = text
            continue

        if role in {"system", "user"}:
            flush_assistant()
            messages.append({"role": role, "content": item.get("content", "")})
            continue

        if role == "assistant":
            flush_assistant(content=item.get("content", ""))
            continue

        if item_type == "function_call":
            pending_calls.append(item)
            continue
        if item_type == "function_call_output":
            flush_assistant()
            messages.append({
                "role": "tool",
                "tool_call_id": item["call_id"],
                "content": item.get("output", ""),
            })
            continue
        if item_type == "message":
            text = extract_output_text([item])
            flush_assistant(content=text or None)

    flush_assistant()
    return messages


def from_chat_message(message):
    output = []
    reasoning = getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None)
    if reasoning:
        output.append({
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": str(reasoning)}],
        })

    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        for call in tool_calls:
            fn = call.function
            output.append({
                "type": "function_call",
                "name": fn.name,
                "call_id": call.id or f"call_{uuid.uuid4().hex[:12]}",
                "arguments": fn.arguments or "{}",
            })
        return output

    content = message.content
    if content:
        output.append({
            "type": "message",
            "content": [{"type": "output_text", "text": content}],
        })
    return output


def _stream_chat_completion(client, kwargs, *, label, stream_sink):
    from langbridge_code.llm.trace import ThoughtEvent

    stream = client.chat.completions.create(**kwargs, stream=True)
    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, str]] = {}
    last_emit = 0.0

    def maybe_emit(kind: str, text: str, *, force: bool = False):
        nonlocal last_emit
        if stream_sink is None or not text:
            return
        now = time.monotonic()
        if not force and now - last_emit < _STREAM_EMIT_INTERVAL_SECONDS:
            return
        last_emit = now
        stream_sink(ThoughtEvent(role=label, kind=kind, text=text))

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning_delta = getattr(delta, "reasoning_content", None)
        if reasoning_delta:
            reasoning_parts.append(reasoning_delta)
            maybe_emit("reasoning_stream", "".join(reasoning_parts))
        if delta.content:
            content_parts.append(delta.content)
            maybe_emit("content_stream", "".join(content_parts))
        for tool_delta in delta.tool_calls or []:
            index = tool_delta.index
            entry = tool_calls.setdefault(
                index,
                {"id": "", "name": "", "arguments": ""},
            )
            if tool_delta.id:
                entry["id"] = tool_delta.id
            function = tool_delta.function
            if function is not None:
                if function.name:
                    entry["name"] += function.name
                if function.arguments:
                    entry["arguments"] += function.arguments
            hint = entry["name"] or "tool"
            if entry["arguments"]:
                hint = f"{hint}({entry['arguments'][:72]})"
            maybe_emit("action_stream", hint, force=True)

    if stream_sink is not None:
        if reasoning_parts:
            maybe_emit("reasoning_stream", "".join(reasoning_parts), force=True)
        if content_parts:
            maybe_emit("content_stream", "".join(content_parts), force=True)

    class _Function:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class _ToolCall:
        def __init__(self, call_id, name, arguments):
            self.id = call_id
            self.function = _Function(name, arguments)

    class _Message:
        def __init__(self, content=None, tool_calls=None, reasoning=None):
            self.content = content
            self.tool_calls = tool_calls
            self.reasoning_content = reasoning

    built_tool_calls = None
    if tool_calls:
        built_tool_calls = [
            _ToolCall(
                entry["id"] or f"call_{uuid.uuid4().hex[:12]}",
                entry["name"],
                entry["arguments"] or "{}",
            )
            for _, entry in sorted(tool_calls.items())
        ]

    message = _Message(
        content="".join(content_parts) or None,
        tool_calls=built_tool_calls,
        reasoning="".join(reasoning_parts) or None,
    )
    return {"output": from_chat_message(message)}


DEFAULT_OPENAI_REASONING = {"summary": "auto"}
DEFAULT_MOONSHOT_THINKING = {"type": "enabled", "keep": "all"}


def create_model_response(
    api_key,
    model,
    agent_input,
    *,
    tool_schemas=None,
    reasoning=None,
    label="agent",
    stream_sink=None,
):
    """Call the provider LLM. Thinking/reasoning is enabled on every request."""
    print_llm_request(label, model, agent_input, tool_schemas)
    client = make_client(api_key)
    last_error = None
    for attempt in range(8):
        try:
            if uses_responses_api():
                kwargs = {
                    "model": model,
                    "input": agent_input,
                    "reasoning": reasoning if reasoning is not None else DEFAULT_OPENAI_REASONING,
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                response = client.responses.create(**kwargs)
                data = response.model_dump(exclude_none=True)
            else:
                kwargs = {
                    "model": model,
                    "messages": to_chat_messages(agent_input),
                    # Moonshot/Kimi: enable thinking + preserve prior reasoning.
                    # kimi-k2.7-code always thinks; keep=all is required for multi-step tools.
                    "extra_body": {"thinking": DEFAULT_MOONSHOT_THINKING},
                }
                if tool_schemas:
                    kwargs["tools"] = to_chat_tools(tool_schemas)
                if API_STREAMING_ENABLED and stream_sink is not None:
                    data = _stream_chat_completion(
                        client,
                        kwargs,
                        label=label,
                        stream_sink=stream_sink,
                    )
                else:
                    response = client.chat.completions.create(**kwargs)
                    message = response.choices[0].message
                    data = {"output": from_chat_message(message)}
            print_llm_response(label, data)
            return data
        except RateLimitError as error:
            if rate_limit_is_non_retryable(error):
                raise ApiQuotaExceeded(quota_exceeded_message(error)) from error
            last_error = error
            time.sleep(min(2 ** attempt, 30))
        except OpenAIError as error:
            raise RuntimeError(str(error)) from error
    if isinstance(last_error, RateLimitError) and rate_limit_is_non_retryable(last_error):
        raise ApiQuotaExceeded(quota_exceeded_message(last_error)) from last_error
    raise RuntimeError(str(last_error)) from last_error
