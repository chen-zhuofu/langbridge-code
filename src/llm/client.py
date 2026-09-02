"""LLM client — OpenAI Responses API, or chat completions for other providers.

Routing:
  - ``openai`` → Responses API (``client.responses.create``)
  - ``moonshot`` / ``deepseek`` / ``anthropic`` → chat completions
    Anthropic uses its OpenAI-compatible endpoint (``api.anthropic.com/v1``).
"""
import os
import re
import time
import uuid

from openai import OpenAI, OpenAIError, RateLimitError

from langbridge_code.llm.debug import print_llm_request, print_llm_response
from langbridge_code.llm.images import to_chat_content, to_responses_input
from langbridge_code.llm.usage import normalize_usage, record_usage


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
# Settings are read via the module (not `from ... import NAME`) because the
# first-run provider selection rebinds them after this module is imported.
from langbridge_code import settings

_STREAM_EMIT_INTERVAL_SECONDS = 0.08

# Claude Code-style: tight default, one silent escalate retry for large writes.
CAPPED_DEFAULT_MAX_TOKENS = 8_000
ESCALATED_MAX_TOKENS = 64_000
_MAX_OUTPUT_HIT_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})


def make_client(api_key, *, base_url=None):
    """Build an OpenAI-compatible client.

    ``base_url`` overrides the active provider default so one process can talk
    to Moonshot, DeepSeek, Anthropic, and OpenAI in the same session.
    """
    kwargs = {
        "api_key": api_key,
        "timeout": settings.API_TIMEOUT_SECONDS,
        "max_retries": settings.API_MAX_RETRIES,
    }
    url = settings.API_BASE_URL if base_url is None else base_url
    if url:
        kwargs["base_url"] = url
    return OpenAI(**kwargs)


def uses_responses_api(provider=None):
    """Only OpenAI uses the Responses API; everyone else uses chat completions."""
    return (provider or settings.API_PROVIDER) == "openai"


def supports_native_tool_search(model, provider=None):
    """Whether this route supports client-executed Responses tool search."""
    resolved = (
        provider
        or settings.infer_provider_for_model(str(model or ""))
        or settings.active_api_provider()
    )
    if resolved != "openai":
        return False
    match = re.search(r"(?:^|/)gpt-(\d+)(?:\.(\d+))?", str(model or "").lower())
    if match is None:
        return False
    version = (int(match.group(1)), int(match.group(2) or 0))
    return version >= (5, 4)


def resolve_max_output_tokens(override=None):
    """Return (max_tokens, env_override).

    Env ``LANGBRIDGE_MAX_OUTPUT_TOKENS`` wins and disables escalate-on-hit.
    """
    env = os.environ.get("LANGBRIDGE_MAX_OUTPUT_TOKENS")
    if env:
        return int(env), True
    if override is not None:
        return int(override), False
    return int(
        getattr(settings, "DEFAULT_MAX_OUTPUT_TOKENS", CAPPED_DEFAULT_MAX_TOKENS)
    ), False


def hit_max_output_tokens(data, finish_reason=None) -> bool:
    """True when the provider stopped because the output token budget was hit."""
    if finish_reason in _MAX_OUTPUT_HIT_REASONS:
        return True
    if not isinstance(data, dict):
        return False
    if data.get("finish_reason") in _MAX_OUTPUT_HIT_REASONS:
        return True
    if data.get("status") == "incomplete":
        details = data.get("incomplete_details") or {}
        if details.get("reason") in _MAX_OUTPUT_HIT_REASONS:
            return True
    return False


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
    thinking models (kimi-k3, kimi-k2.7-code with keep=all) keep continuity
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
            messages.append(
                {"role": role, "content": to_chat_content(item.get("content", ""))}
            )
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

    stream_kwargs = dict(kwargs)
    stream_kwargs["stream"] = True
    # Prefer usage on the final chunk when the provider supports it.
    stream_kwargs["stream_options"] = {"include_usage": True}
    try:
        stream = client.chat.completions.create(**stream_kwargs)
    except OpenAIError:
        stream_kwargs.pop("stream_options", None)
        stream = client.chat.completions.create(**stream_kwargs)
    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, str]] = {}
    finish_reason = None
    usage = None
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
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = chunk_usage
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        choice_finish_reason = getattr(choice, "finish_reason", None)
        if choice_finish_reason:
            finish_reason = choice_finish_reason
        delta = choice.delta
        if delta is None:
            continue
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
    data = {"output": from_chat_message(message), "finish_reason": finish_reason}
    normalized = normalize_usage(usage)
    if normalized:
        data["usage"] = normalized
    return data


# OpenAI Responses API: GPT-5.6 supports effort "max"; earlier GPT-5.x tops at "xhigh".
DEFAULT_OPENAI_REASONING_SUMMARY = "auto"
DEFAULT_OPENAI_REASONING_EFFORT = "xhigh"
DEFAULT_OPENAI_REASONING_EFFORT_MAX = "max"
# Moonshot K2.x: keep=all is required so prior reasoning survives multi-step tools.
DEFAULT_MOONSHOT_THINKING = {"type": "enabled", "keep": "all"}
# Moonshot K3: always thinks; configure effort via top-level reasoning_effort
# (low|high|max). Do not send the K2.x thinking/keep body — K3 rejects it (400).
DEFAULT_KIMI_K3_REASONING_EFFORT = "max"
# DeepSeek V4: thinking switch + reasoning_effort (high|max). "keep" is not supported.
DEFAULT_DEEPSEEK_THINKING = {"type": "enabled"}
DEFAULT_DEEPSEEK_REASONING_EFFORT = "max"
# Anthropic Fable 5+: thinking is always on; depth is output_config.effort (…|max).
DEFAULT_ANTHROPIC_OUTPUT_CONFIG = {"effort": "max"}


def _is_kimi_k3(model: str | None) -> bool:
    name = (model or "").strip().lower().rsplit("/", 1)[-1]
    return name in {"kimi-k3", "k3"} or name.startswith("kimi-k3-")


def _is_gpt_5_6(model: str | None) -> bool:
    name = (model or "").strip().lower().rsplit("/", 1)[-1]
    return name == "gpt-5.6" or name.startswith("gpt-5.6-")


def _openai_reasoning(model: str | None = None) -> dict:
    """Highest think effort for the model family."""
    effort = (
        DEFAULT_OPENAI_REASONING_EFFORT_MAX
        if _is_gpt_5_6(model)
        else DEFAULT_OPENAI_REASONING_EFFORT
    )
    return {"effort": effort, "summary": DEFAULT_OPENAI_REASONING_SUMMARY}


def _chat_extra_body(
    model: str | None = None,
    *,
    provider: str | None = None,
    reasoning: dict | None = None,
):
    """Provider-specific thinking config, with an optional low-budget override."""
    resolved = provider or settings.infer_provider_for_model(model) or settings.API_PROVIDER
    requested_effort = str((reasoning or {}).get("effort", "")).lower()
    low_budget = requested_effort in {"none", "minimal", "low"}
    if resolved == "moonshot":
        if _is_kimi_k3(model):
            return {
                "reasoning_effort": "low" if low_budget else DEFAULT_KIMI_K3_REASONING_EFFORT
            }
        if low_budget:
            return {"thinking": {"type": "disabled"}}
        return {"thinking": DEFAULT_MOONSHOT_THINKING}
    if resolved == "deepseek":
        if low_budget:
            return {"thinking": {"type": "disabled"}}
        return {
            "thinking": DEFAULT_DEEPSEEK_THINKING,
            "reasoning_effort": DEFAULT_DEEPSEEK_REASONING_EFFORT,
        }
    if resolved == "anthropic":
        return {
            "output_config": (
                {"effort": "low"} if low_budget else DEFAULT_ANTHROPIC_OUTPUT_CONFIG
            )
        }
    return None


def create_model_response(
    api_key,
    model,
    agent_input,
    *,
    tool_schemas=None,
    reasoning=None,
    max_output_tokens=None,
    label="agent",
    stream_sink=None,
):
    """Call the provider LLM. Thinking/reasoning is enabled on every request.

    Routes by ``model`` id so roles can use another provider than the session
    default (e.g. kimi main + deepseek-v4-flash explorer).
    """
    print_llm_request(label, model, agent_input, tool_schemas)
    route = settings.resolve_llm_route(model, api_key)
    if not route.get("api_key"):
        raise ValueError(
            f"No API key for provider {route['provider']!r} (model {model!r}). "
            f"Add it under api_keys.{route['provider']} in ~/.langbridge/config.json."
        )
    client = make_client(route["api_key"], base_url=route["base_url"] or None)
    provider = route["provider"]
    routed_model = route.get("model") or model
    max_tokens, env_override = resolve_max_output_tokens(max_output_tokens)
    default_cap = int(
        getattr(settings, "DEFAULT_MAX_OUTPUT_TOKENS", CAPPED_DEFAULT_MAX_TOKENS)
    )
    escalated_cap = int(
        getattr(settings, "ESCALATED_MAX_OUTPUT_TOKENS", ESCALATED_MAX_TOKENS)
    )
    escalated = False
    last_error = None
    for attempt in range(8):
        try:
            if uses_responses_api(provider):
                kwargs = {
                    "model": routed_model,
                    "input": to_responses_input(agent_input),
                    "reasoning": (
                        reasoning
                        if reasoning is not None
                        else _openai_reasoning(routed_model)
                    ),
                    "max_output_tokens": max_tokens,
                }
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                response = client.responses.create(**kwargs)
                data = response.model_dump(exclude_none=True)
                finish_reason = None
                usage = normalize_usage(data.get("usage") or getattr(response, "usage", None))
            else:
                kwargs = {
                    "model": routed_model,
                    "messages": to_chat_messages(agent_input),
                    "max_tokens": max_tokens,
                }
                extra_body = _chat_extra_body(
                    routed_model,
                    provider=provider,
                    reasoning=reasoning,
                )
                if extra_body:
                    kwargs["extra_body"] = extra_body
                if tool_schemas:
                    kwargs["tools"] = to_chat_tools(tool_schemas)
                if settings.API_STREAMING_ENABLED and stream_sink is not None:
                    data = _stream_chat_completion(
                        client,
                        kwargs,
                        label=label,
                        stream_sink=stream_sink,
                    )
                    finish_reason = data.pop("finish_reason", None)
                    usage = data.get("usage")
                else:
                    response = client.chat.completions.create(**kwargs)
                    message = response.choices[0].message
                    finish_reason = response.choices[0].finish_reason
                    data = {"output": from_chat_message(message)}
                    usage = normalize_usage(getattr(response, "usage", None))
            if (
                not env_override
                and not escalated
                and max_tokens == default_cap
                and hit_max_output_tokens(data, finish_reason)
            ):
                escalated = True
                max_tokens = escalated_cap
                continue
            if usage:
                data["usage"] = usage
                record_usage(usage, label=label)
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
