"""Long-term memory: project memory (repo) + user memory (home directory).

Layout per scope:
  memory.md            — index: one "- <file>.md: <one-line title>" per entry
  memory/<slug>.md     — entry body

User memory is about the PERSON using LangBridge — their preferences and
standing feedback across projects. Project memory is specific to this repo.
Both belong to the main agent only; subagents have neither.

Reads (prefetch): one LLM pass looks at the combined memory.md index and the
current task, picks relevant files; the workflow reads them into a <memory>
block prepended to the active context. Re-run after every compaction — the
memories may have been updated mid-turn.

Two write phases:
  1. In-turn: the main agent calls the ``remember`` tool whenever it learns
     something durable.
  2. At turn end: a forked memory-writer agent reviews the turn asynchronously
     (reusing the live context — prefix cache; a fresh LLM cannot read the raw
     traces), writes anything missed, and exits.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from langbridge_code.settings import PROJECT_MEMORY_PATH, USER_MEMORY_PATH

SCOPE_PROJECT = "project"
SCOPE_USER = "user"
SCOPES = (SCOPE_USER, SCOPE_PROJECT)

MAX_ENTRY_CHARS = 8_000
MAX_BLOCK_CHARS = 24_000

_MEMORY_REF_RE = re.compile(r"\b(user|project)/([\w.\-]+\.md)\b")
_memory_lock = threading.Lock()


def memory_index_path(scope: str) -> Path:
    if scope == SCOPE_PROJECT:
        return Path(PROJECT_MEMORY_PATH)
    if scope == SCOPE_USER:
        return Path(USER_MEMORY_PATH)
    raise ValueError(f"Unknown memory scope: {scope}")


def memory_dir(scope: str) -> Path:
    return memory_index_path(scope).parent / "memory"


def read_memory_index(scope: str) -> str:
    path = memory_index_path(scope)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def memory_index_text() -> str:
    """Combined user + project index for the prefetch pass (empty if no memory)."""
    parts = []
    for scope in SCOPES:
        index = read_memory_index(scope)
        if index:
            parts.append(f"## {scope}\n{index}")
    return "\n\n".join(parts)


def _slugify(title: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", (title or "").strip().lower()).strip("-")
    return (slug[:max_len].strip("-")) or "memory"


def write_memory(scope: str, title: str, content: str) -> str:
    """Write (or overwrite) one memory entry file and update the scope index."""
    if scope not in SCOPES:
        return f"Unknown memory scope '{scope}'. Use 'project' or 'user'."
    title = " ".join((title or "").split()).strip()
    body = (content or "").strip()
    if not title or not body:
        return "Memory needs both a title and content; nothing saved."

    name = f"{_slugify(title)}.md"
    line = f"- {name}: {title}"
    with _memory_lock:
        directory = memory_dir(scope)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        lines = [entry for entry in read_memory_index(scope).splitlines() if entry.strip()]
        for position, entry in enumerate(lines):
            if entry.strip().startswith(f"- {name}:"):
                lines[position] = line
                break
        else:
            lines.append(line)
        index_path = memory_index_path(scope)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"Saved to {scope} memory: {scope}/{name} — {title}"


def read_memory_entry(scope: str, name: str) -> str:
    if scope not in SCOPES or "/" in name or ".." in name:
        return ""
    path = memory_dir(scope) / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


PREFETCH_SYSTEM = """You select which memory files are relevant to a task.

You get a memory index (user scope = facts about this LangBridge user; project
scope = facts about this project) and the current task. Reply with one file
reference per line in the form user/<file>.md or project/<file>.md. Pick only
files likely to help with THIS task. If nothing is relevant, reply exactly NONE."""


def prefetch_memory(api_key, model, task: str) -> str:
    """One LLM pass over memory.md, then read the chosen files into one block."""
    index = memory_index_text()
    if not index.strip() or not (api_key and model):
        return ""
    try:
        from langbridge_code.llm.client import create_model_response
        from langbridge_code.llm.parse import extract_output_text, truncate_text

        response = create_model_response(
            api_key,
            model,
            [
                {"role": "system", "content": PREFETCH_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Memory index:\n{truncate_text(index, 20_000)}\n\n"
                        f"Task:\n{truncate_text((task or '').strip(), 4_000)}"
                    ),
                },
            ],
            label="memory prefetch",
        )
        reply = extract_output_text(response.get("output", [])).strip()
    except Exception:
        return ""
    if not reply or reply.upper().startswith("NONE"):
        return ""

    sections = []
    seen = set()
    total = 0
    for scope, name in _MEMORY_REF_RE.findall(reply):
        ref = f"{scope}/{name}"
        if ref in seen:
            continue
        seen.add(ref)
        body = read_memory_entry(scope, name)
        if not body:
            continue
        body = body[:MAX_ENTRY_CHARS]
        total += len(body)
        sections.append(f"## {ref}\n{body}")
        if total >= MAX_BLOCK_CHARS:
            break
    return "\n\n".join(sections)


MEMORY_EXTRACT_INSTRUCTION = """The turn above is over. You are a forked memory writer for this session.
Extract durable memories worth keeping for future sessions. Two scopes:
- user: general facts about this LangBridge user (cross-project preferences,
  standing feedback) — about the human, never about the assistant.
- project: facts specific to this project (conventions, commands, gotchas,
  standing decisions, where things are tracked).

Only record what will still matter in a later session. Skip task status and
anything already covered by the plan or progress notes. At most 3 memories.
Do not repeat memories already shown in the <memory> block above.

Output format (repeat per memory), or exactly NONE:
MEMORY_SCOPE: user|project
MEMORY_TITLE: <short title>
MEMORY_CONTENT: <1-4 sentences, in the user's language>"""

_MEMORY_EXTRACT_RE = re.compile(
    r"MEMORY_SCOPE:\s*(user|project)\s*\n\s*MEMORY_TITLE:\s*(.+?)\s*\n\s*MEMORY_CONTENT:\s*(.+?)(?=\nMEMORY_SCOPE:|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def parse_memory_extraction(reply: str) -> list[tuple[str, str, str]]:
    if not reply or reply.strip().upper().startswith("NONE"):
        return []
    memories = []
    for scope, title, content in _MEMORY_EXTRACT_RE.findall(reply):
        memories.append((scope.lower(), title.strip(), content.strip()))
    return memories[:3]


def extract_and_write_memories(api_key, model, messages) -> list[str]:
    """One-pass memory-writer fork on the live context: extract, write, report."""
    from langbridge_code.agents.common.fork import fork_one_pass

    reply = fork_one_pass(
        api_key,
        model,
        messages,
        MEMORY_EXTRACT_INSTRUCTION,
        label="memory writer fork",
    )
    return [
        write_memory(scope, title, content)
        for scope, title, content in parse_memory_extraction(reply)
    ]


def schedule_memory_extraction(api_key, model, messages) -> None:
    """Fork the memory-writer agent in the background; it exits when done."""
    if not (api_key and model and messages):
        return
    snapshot = list(messages)

    def worker() -> None:
        try:
            extract_and_write_memories(api_key, model, snapshot)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True, name="memory-writer").start()
