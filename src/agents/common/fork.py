"""Forks of a live agent context (prefix-cache friendly).

A fork reuses the agent's message list verbatim and appends one instruction,
so the provider can serve the shared prefix from cache. Tool-using forks handle
bounded side workflows such as memory maintenance and Edit-restricted progress
notes. A fresh LLM cannot read the raw traces, but the live context already has
everything.

When a session TraceContext is active, fork model steps are written to
session.md under the fork label (same channel as agent traces).
"""
from __future__ import annotations

import json
from pathlib import Path

from langbridge_code.agents.common import control
from langbridge_code.settings import MAX_AGENT_STEPS
from langbridge_code.tools.common.description import without_description

# Progress-note Edit loops are short; bound them so a stubborn model cannot
# burn the full main-agent step budget on denials.
_SESSION_MEMORY_MAX_STEPS = 8
_PROGRESS_NOTE_MAX_STEPS = _SESSION_MEMORY_MAX_STEPS  # back-compat


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


def _session_memory_edit_tool(allowed_path: Path):
    """Edit that only mutates the exact session_memory.md file (absolute path OK)."""
    allowed = allowed_path.resolve()
    deny = f"only Edit on {allowed} is allowed"

    def _resolve(path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate.resolve()
        # Prompt always gives the absolute path; also accept the bare filename
        # or a path under the notes directory (model sometimes shortens it).
        under_notes = (allowed.parent / candidate).resolve()
        if under_notes == allowed:
            return allowed
        if candidate.name == allowed.name:
            return allowed
        # Legacy progress.md edits that target the same notes dir.
        if candidate.name in {"session_memory.md", "progress.md"} and (
            under_notes.parent == allowed.parent
        ):
            return allowed
        return under_notes

    def Edit(path, old_string, new_string, replace_all=False):
        if not old_string:
            raise ValueError("old_string must not be empty")
        if old_string == new_string:
            raise ValueError(
                "No changes to make: old_string and new_string are exactly the same."
            )
        target = _resolve(path)
        if target != allowed:
            return deny
        if not allowed.exists():
            raise FileNotFoundError(f"No such file: {allowed}")
        text = allowed.read_text(encoding="utf-8")
        matches = text.count(old_string)
        if matches == 0:
            raise ValueError("old_string was not found")
        if matches > 1 and not replace_all:
            raise ValueError(
                f"Found {matches} matches of the string to replace, but replace_all "
                "is false. To replace all occurrences, set replace_all to true. "
                "To replace only one occurrence, provide more context to uniquely "
                "identify it."
            )
        if replace_all:
            updated = text.replace(old_string, new_string)
            count = matches
        else:
            updated = text.replace(old_string, new_string, 1)
            count = 1
        allowed.write_text(updated, encoding="utf-8")
        occurrence = "occurrence" if count == 1 else "occurrences"
        return f"Edited {allowed}: replaced {count} {occurrence}."

    return Edit


def _session_memory_tools(allowed_path: Path, tool_schemas) -> dict:
    """Parent schemas for cache match; only Edit on session_memory.md works."""
    allowed = allowed_path.resolve()
    deny_message = f"only Edit on {allowed} is allowed"

    def deny(**_kwargs):
        return deny_message

    tools = {}
    for schema in tool_schemas or ():
        name = schema.get("name")
        if name:
            tools[name] = deny
    tools["Edit"] = _session_memory_edit_tool(allowed)
    return tools


def _session_memory_schemas(tool_schemas) -> list:
    """Keep parent schemas for prompt-cache key match; ensure Edit is present."""
    schemas = list(tool_schemas or [])
    if not any(schema.get("name") == "Edit" for schema in schemas):
        from langbridge_code.tools import filesystem

        edit_schema = next(
            (item for item in filesystem.TOOL_SCHEMAS if item.get("name") == "Edit"),
            None,
        )
        if edit_schema is not None:
            schemas.append(edit_schema)
    return schemas


def fork_session_memory(
    api_key,
    model,
    messages: list[dict],
    *,
    run_log_path,
    task_name: str | None = None,
    role: str | None = None,
    turn_id: int | None = None,
    tool_schemas=None,
    label: str = "session memory fork",
    max_steps: int = _SESSION_MEMORY_MAX_STEPS,
) -> str:
    """Session-memory update: Edit-only fork on session_memory.md.

    Seeds the section template when the file is empty, runs a tool-using fork
    that may only Edit that file, and returns a ``Noted...`` / no-op string
    based on whether the file content changed.
    """
    from langbridge_code.prompt.fork import build_session_memory_instruction
    from langbridge_code.util.progress import (
        ensure_session_memory_template,
        read_session_memory,
        session_memory_edit_succeeded,
        session_memory_path,
    )

    path = session_memory_path(run_log_path, task_name, role=role)
    if path is None:
        return "No session directory; note not recorded."
    before = ensure_session_memory_template(run_log_path, task_name, role=role)
    schemas = _session_memory_schemas(tool_schemas)
    tools = _session_memory_tools(path, schemas)
    instruction = build_session_memory_instruction(
        notes_path=str(path.resolve()),
        current_notes=before.rstrip() + "\n",
        task=bool(task_name),
    )
    try:
        fork_agent(
            api_key,
            model,
            list(messages),
            instruction,
            tool_schemas=schemas,
            tools=tools,
            label=label,
            max_steps=max_steps,
        )
    except Exception as error:
        return f"Session memory fork failed: {error}"
    after = read_session_memory(run_log_path, task_name, role=role)
    return session_memory_edit_succeeded(
        before,
        after,
        run_log_path=run_log_path,
        turn_id=turn_id,
        task_name=task_name,
    )


def fork_progress_note(*args, **kwargs) -> str:
    """Back-compat alias for ``fork_session_memory``."""
    kwargs.setdefault("label", "session memory fork")
    return fork_session_memory(*args, **kwargs)


# Private aliases for older callers/tests.
_progress_edit_tool = _session_memory_edit_tool
_progress_note_tools = _session_memory_tools
_progress_note_schemas = _session_memory_schemas
