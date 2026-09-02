"""Session-scoped raw traces: traces.md (main agent, read on cold-start resume)."""
from __future__ import annotations

import json
import re
import threading

from langbridge_code.context.common.budget import estimate_tokens
from langbridge_code.llm.model_context import model_context_window
from langbridge_code.settings import TRACES_RESUME_MAX_FRACTION
from langbridge_code.util.artifacts import traces_md_path as artifact_traces_md_path

TRACES_HEADER = "# Session traces\n"
_PENDING_USER_KEY = "_langbridge_pending_user"
PROGRESS_BOUNDARY_RE = re.compile(
    r"^## Progress boundary \(turn (\d+)\)\s*$",
    re.MULTILINE,
)
_TURN_SECTION_RE = re.compile(r"^## Turn \d+\s*$", re.MULTILINE)
_TURN_ID_RE = re.compile(r"^## Turn (\d+)\s*$", re.MULTILINE)
_JSON_BLOCK_RE = re.compile(r"^```json\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)

_traces_lock = threading.Lock()


def traces_md_path(run_log_path):
    return artifact_traces_md_path(run_log_path)


def read_traces(run_log_path) -> str:
    if not run_log_path:
        return ""
    path = traces_md_path(run_log_path)
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_traces(run_log_path, content: str) -> None:
    path = traces_md_path(run_log_path)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _message_text(item: dict) -> str:
    """Plain text of a user/assistant message (string or content-part list)."""
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def _message_images(item: dict) -> list[str]:
    """Local attachment paths from internal multimodal message content."""
    content = item.get("content")
    if not isinstance(content, list):
        return []
    return [
        str(part.get("image_path"))
        for part in content
        if isinstance(part, dict)
        and part.get("type") == "input_image"
        and part.get("image_path")
    ]


# Engine-injected user messages (hooks, status blocks, pinned background);
# not part of the human conversation, so hidden from resume replay / seed.
_INJECTED_PREFIXES = (
    "[HOOK]",
    "[CONTEXT_STATUS]",
    "<background>",
    "<background_tool_results>",
    "<assigned_task>",
    "<subagent_state>",
    "<session_memory>",
    "<progress>",  # legacy pin tag
    "<memory>",
    "<skill_index>",
    "<invoked_skills>",
    "<history_conversation>",
)


def _json_blocks_with_turn(content: str):
    """Yield ``(match, turn_id, items)`` for parseable trace JSON blocks."""
    headings = list(_TURN_ID_RE.finditer(content))
    heading_index = 0
    turn_id = None
    for match in _JSON_BLOCK_RE.finditer(content):
        while (
            heading_index < len(headings)
            and headings[heading_index].start() < match.start()
        ):
            turn_id = int(headings[heading_index].group(1))
            heading_index += 1
        try:
            items = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(items, list):
            yield match, turn_id, items


def read_conversation_items(
    run_log_path, *, exclude_pending_turn_id: int | None = None
) -> list[dict]:
    """Ordered displayable user/assistant messages from raw session traces.

    Each item contains ``role``, ``text``, the durable backend ``turn_id`` it
    belongs to, and (when present) local ``images``. Tool calls, tool outputs,
    reasoning, and engine-injected user messages are skipped.
    """
    content = read_traces(run_log_path)
    conversation: list[dict] = []
    for _match, turn_id, items in _json_blocks_with_turn(content):
        for item in items:
            if not isinstance(item, dict):
                continue
            if (
                item.get(_PENDING_USER_KEY)
                and exclude_pending_turn_id is not None
                and turn_id == exclude_pending_turn_id
            ):
                continue
            role = item.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _message_text(item).strip()
            images = _message_images(item)
            if not text and not images:
                continue
            if role == "user" and text.startswith(_INJECTED_PREFIXES):
                continue
            entry = {"role": role, "text": text, "turn_id": turn_id}
            if images:
                entry["images"] = images
            conversation.append(entry)
    return conversation


def read_conversation(
    run_log_path, *, exclude_pending_turn_id: int | None = None
) -> list[tuple[str, str]]:
    """Back-compatible text-only view of ``read_conversation_items``."""
    return [
        (str(item["role"]), str(item.get("text", "")))
        for item in read_conversation_items(
            run_log_path, exclude_pending_turn_id=exclude_pending_turn_id
        )
        if str(item.get("text", "")).strip()
    ]


def conversation_to_raw_rounds(
    conversation: list[tuple[str, str]],
) -> list[list[dict]]:
    """Turn clean (role, text) pairs into ContextStack raw rounds.

    Consecutive users without an assistant reply become a user-only round.
    """
    rounds: list[list[dict]] = []
    pending_user: str | None = None
    for role, text in conversation or []:
        body = (text or "").strip()
        if not body or role not in ("user", "assistant"):
            continue
        if role == "user":
            if pending_user is not None:
                rounds.append([{"role": "user", "content": pending_user}])
            pending_user = body
            continue
        items: list[dict] = []
        if pending_user is not None:
            items.append({"role": "user", "content": pending_user})
            pending_user = None
        items.append({"role": "assistant", "content": body})
        rounds.append(items)
    if pending_user is not None:
        rounds.append([{"role": "user", "content": pending_user}])
    return rounds


def format_history_conversation(conversation: list[tuple[str, str]]) -> str:
    """Render clean Q&A as a single tagged transcript body (no outer tags)."""
    blocks: list[str] = []
    for role, text in conversation or []:
        body = (text or "").strip()
        if not body or role not in ("user", "assistant"):
            continue
        label = "USER" if role == "user" else "ASSISTANT"
        blocks.append(f"{label}: {body}")
    return "\n\n".join(blocks)


def _filter_round_messages(round_messages: list[dict]) -> list[dict]:
    filtered = []
    for item in round_messages or []:
        if not isinstance(item, dict):
            continue
        if item.get("role") == "system":
            continue
        filtered.append(item)
    return filtered


def _json_block(messages: list[dict]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, indent=2)
    return f"```json\n{payload}\n```"


def _is_displayable_user(item: dict) -> bool:
    if not isinstance(item, dict) or item.get("role") != "user":
        return False
    text = _message_text(item).strip()
    if text.startswith(_INJECTED_PREFIXES):
        return False
    return bool(text or _message_images(item))


def _finalize_pending_user(content: str, turn_id: int) -> tuple[str, bool]:
    """Remove the pending marker from the eager user message for one turn."""
    replacements: list[tuple[int, int, str]] = []
    found = False
    for match, block_turn_id, items in _json_blocks_with_turn(content):
        if block_turn_id != turn_id:
            continue
        changed = False
        for item in items:
            if isinstance(item, dict) and item.pop(_PENDING_USER_KEY, None):
                changed = True
                found = True
        if changed:
            replacements.append((match.start(), match.end(), _json_block(items)))
    for start, end, replacement in reversed(replacements):
        content = content[:start] + replacement + content[end:]
    return content, found


def append_user_message(
    run_log_path,
    turn_id: int,
    text: str,
    *,
    image_paths=None,
) -> None:
    """Persist a user message before any model work begins.

    The marker lets the first completed agent round replace its expanded copy
    with this exact UI text. If the process stops early, resume replay still
    has the original prompt and attachments.
    """
    from langbridge_code.llm.images import user_content_with_images

    content = user_content_with_images(text, image_paths)
    append_raw_round(
        run_log_path,
        turn_id,
        [
            {
                "role": "user",
                "content": content,
                _PENDING_USER_KEY: True,
            }
        ],
    )


def append_raw_round(run_log_path, turn_id: int, round_messages: list[dict]) -> None:
    """Append one main-agent raw round (no system) under ``## Turn N``."""
    filtered = _filter_round_messages(round_messages)
    if not run_log_path or not filtered:
        return
    turn = int(turn_id or 0)
    heading = f"## Turn {turn}"
    with _traces_lock:
        existing = read_traces(run_log_path).strip()
        incoming_pending = any(item.get(_PENDING_USER_KEY) for item in filtered)
        if not incoming_pending and filtered and _is_displayable_user(filtered[0]):
            existing, had_pending_user = _finalize_pending_user(existing, turn)
            if had_pending_user:
                # The eager copy is the canonical UI prompt. The context copy
                # may be slash-expanded, so retain only the later round items.
                filtered = filtered[1:]
                if not filtered:
                    write_traces(run_log_path, existing)
                    return
        block = _json_block(filtered)
        if not existing or existing == TRACES_HEADER.strip():
            body = TRACES_HEADER + heading + "\n\n" + block + "\n"
            write_traces(run_log_path, body)
            return
        # Append under existing trailing turn heading when it matches.
        last_heading = None
        for match in _TURN_SECTION_RE.finditer(existing):
            last_heading = match
        if last_heading and last_heading.group(0).strip() == heading:
            body = existing.rstrip() + "\n\n" + block + "\n"
        else:
            body = existing.rstrip() + "\n\n" + heading + "\n\n" + block + "\n"
        write_traces(run_log_path, body)


def append_session_memory_boundary(run_log_path, turn_id: int) -> None:
    """Mark that session_memory.md now covers traces through this turn."""
    if not run_log_path:
        return
    turn = int(turn_id or 0)
    marker = f"## Progress boundary (turn {turn})"
    with _traces_lock:
        existing = read_traces(run_log_path).strip()
        if not existing or existing == TRACES_HEADER.strip():
            write_traces(run_log_path, TRACES_HEADER + marker + "\n")
            return
        if existing.rstrip().endswith(marker):
            return
        write_traces(run_log_path, existing.rstrip() + "\n\n" + marker + "\n")


def append_progress_boundary(run_log_path, turn_id: int) -> None:
    """Back-compat alias for ``append_session_memory_boundary``."""
    append_session_memory_boundary(run_log_path, turn_id)


def last_traces_turn_id(run_log_path) -> int:
    """Highest turn id recorded in traces.md (0 if none)."""
    content = read_traces(run_log_path)
    ids = [int(match.group(1)) for match in _TURN_ID_RE.finditer(content)]
    ids.extend(int(match.group(1)) for match in PROGRESS_BOUNDARY_RE.finditer(content))
    return max(ids, default=0)


def _content_after_last_boundary(content: str) -> str:
    matches = list(PROGRESS_BOUNDARY_RE.finditer(content))
    if not matches:
        return content
    last = matches[-1]
    return content[last.end() :].lstrip("\n")


def _trim_head_to_budget(text: str, max_tokens: int) -> str:
    if max_tokens <= 0 or not text.strip():
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    parts = [part for part in re.split(r"\n{2,}", text.strip()) if part.strip()]
    if not parts:
        return ""
    kept: list[str] = []
    for part in reversed(parts):
        candidate = [part, *kept]
        joined = "\n\n".join(candidate)
        if estimate_tokens(joined) <= max_tokens:
            kept = candidate
            continue
        if not kept:
            approx_chars = max(64, max_tokens * 4)
            return text[-approx_chars:].lstrip()
        break
    return "\n\n".join(kept).strip()


def build_resume_background(run_log_path, *, model: str, progress: str = "") -> str:
    """Background text for a cold start (new session object / resume).

    When the full raw traces fit the resume budget on their own, use them
    directly — the session memory are just a summary of the same rounds.
    Otherwise fall back to session memory plus the traces recorded after the
    last session-memory boundary (rounds not yet summarized into session_memory.md),
    trimmed from the head when even those exceed the remaining budget.
    """
    progress = (progress or "").strip()
    content = read_traces(run_log_path).strip()
    if not content or content == TRACES_HEADER.strip():
        return progress

    window = model_context_window(model)
    if window is None:
        from langbridge_code.settings import COMPACT_THRESHOLD_TOKENS

        window = COMPACT_THRESHOLD_TOKENS
    budget = max(1, int(window * TRACES_RESUME_MAX_FRACTION))
    if estimate_tokens(content) <= budget:
        return content

    # Rounds after the last boundary are the only ones session_memory.md does not
    # cover; a healthy shutdown leaves nothing here and progress alone suffices.
    after = _content_after_last_boundary(content).strip()
    if not after:
        return progress
    remaining = max(0, budget - estimate_tokens(progress))
    tail = _trim_head_to_budget(after, remaining)
    if not tail.strip():
        return progress
    heading = "## Raw main-agent traces since the last session memory update"
    return (progress + "\n\n" if progress else "") + heading + "\n\n" + tail.strip()
