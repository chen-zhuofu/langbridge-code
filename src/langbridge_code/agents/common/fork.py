"""Forks of a live agent context (prefix-cache friendly).

A fork reuses the agent's message list verbatim and appends one instruction,
so the provider can serve the shared prefix from cache. One-pass forks write
progress notes (they may pass the parent's tool_schemas for cache-key match,
but never execute tools). Tool-using forks handle bounded side workflows such
as memory maintenance. A fresh LLM cannot read the raw traces, but the live
context already has everything.

When a session TraceContext is active, fork model steps are written to
session.md under the fork label (same channel as agent traces).
"""
from __future__ import annotations

import json

import re

from langbridge_code.agents.common import control
from langbridge_code.settings import MAX_AGENT_STEPS
from langbridge_code.tools.common.description import without_description

# One-pass forks may pass the parent's tool_schemas for prompt-cache key match
# (Claude Code compact path). Never execute tools — reject and retry.
_ONE_PASS_RETRIES = 2
_TOOL_MARKUP_RE = re.compile(
    r"(?is)("
    r"\bDSML\b"
    r"|<\s*/?\s*tool_call\b"
    r"|function_call\b"
    r"|tool_calls\b"
    r"|invoke\s+name\s*="
    r"|<\|\w*tool"
    r")"
)
_REJECT_TOOL_CALLS = (
    "Rejected: do NOT call any tools. Respond with plain markdown note text "
    "only. Tool calls waste your turn and fail the task. Reply again with "
    "the note only — no XML, DSML, or invoke markup."
)


def _trace_fork_input(label: str, instruction: str, message_count: int) -> None:
    from langbridge_code.util.trace_log import write_line

    preview = (instruction or "").strip()
    write_line(
        label,
        f"input: live context ({message_count} messages) + instruction\n{preview}",
    )


def _trace_fork_output(label: str, output) -> None:
    from langbridge_code.util.trace_log import log_from_step_output

    log_from_step_output(label, output)


def _trace_fork_tool(label: str, name: str, result: str) -> None:
    from langbridge_code.util.trace_log import log_tool_result

    log_tool_result(label, name, result)


def _trace_fork_reject(label: str, reason: str) -> None:
    from langbridge_code.util.trace_log import write_line

    write_line(label, f"reject: {reason}")


def _one_pass_tool_call_reason(output, text: str) -> str | None:
    """Return a short reason if the one-pass reply tried to use tools."""
    calls = [item for item in output if item.get("type") == "function_call"]
    if calls:
        names = ", ".join(str(item.get("name") or "tool") for item in calls)
        return f"function_call ({names})"
    if text and _TOOL_MARKUP_RE.search(text):
        return "tool markup in text"
    return None


def fork_one_pass(
    api_key,
    model,
    messages: list[dict],
    instruction: str,
    *,
    label: str = "fork",
    tool_schemas=None,
) -> str:
    """One LLM pass on the live prefix. Retries if the model emits tool calls.

    Pass the parent's ``tool_schemas`` when available so the request shares the
    prompt-cache key with the live agent. Tools are never executed here.
    """
    from langbridge_code.llm.client import create_model_response
    from langbridge_code.llm.parse import extract_output_text

    forked = list(messages) + [{"role": "user", "content": instruction}]
    _trace_fork_input(label, instruction, len(messages))
    for attempt in range(_ONE_PASS_RETRIES + 1):
        data = create_model_response(
            api_key,
            model,
            forked,
            tool_schemas=tool_schemas,
            label=label,
        )
        output = list(data.get("output") or [])
        _trace_fork_output(label, output)
        text = extract_output_text(output).strip()
        reason = _one_pass_tool_call_reason(output, text)
        if reason is None:
            return text
        _trace_fork_reject(label, reason)
        if attempt >= _ONE_PASS_RETRIES:
            break
        forked.extend(output)
        forked.append({"role": "user", "content": _REJECT_TOOL_CALLS})
    # Do not record tool markup as a progress note.
    return ""


def fork_agent(
    api_key,
    model,
    messages: list[dict],
    instruction: str,
    *,
    tool_schemas,
    tools,
    label: str = "fork agent",
    max_steps: int = MAX_AGENT_STEPS,
) -> str:
    """Fork live context and run a tool-using agent until its final reply."""
    from langbridge_code.llm.client import create_model_response
    from langbridge_code.llm.parse import extract_output_text

    forked = list(messages) + [{"role": "user", "content": instruction}]
    _trace_fork_input(label, instruction, len(messages))
    for _ in range(max_steps):
        control.checkpoint()
        data = control.run_interruptible(
            lambda: create_model_response(
                api_key,
                model,
                forked,
                tool_schemas=tool_schemas,
                reasoning={"summary": "auto"},
                label=label,
            )
        )
        output = list(data.get("output", []))
        forked.extend(output)
        _trace_fork_output(label, output)
        calls = [item for item in output if item.get("type") == "function_call"]
        if not calls:
            return extract_output_text(output).strip()
        for call in calls:
            call_id = call.get("call_id")
            name = call.get("name") or "tool"
            try:
                arguments = without_description(json.loads(call.get("arguments") or "{}"), name)
                if name not in tools:
                    raise ValueError(f"Unknown {label} tool: {name}")
                result = tools[name](**arguments)
            except Exception as error:
                result = f"Tool error: {error}"
            _trace_fork_tool(label, name, str(result))
            forked.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result,
                }
            )
    return f"{label} stopped: max steps."
