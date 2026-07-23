"""Long-term memory: project memory (repo) + user memory (home directory).

Layout per scope:
  memory.md            — index: one "- <file>.md: <one-line title>" per entry
  memory/<slug>.md     — entry body

User memory is about the PERSON using LangBridge — their preferences and
standing feedback across projects. Project memory is specific to this repo.
The main agent, workers, and reviewers all share the same indexes: they
prefetch into a <memory> block and may fork the Memory Writer to update them.

Reads (prefetch): one LLM pass looks at the combined memory.md index and the
current task, picks relevant files; the workflow reads them into a <memory>
block prepended to the active context. Re-run after every compaction — the
memories may have been updated mid-turn.

Memory scope and type are independent:
  user scope    — global across projects; allows user/feedback/reference.
  project scope — current repository; allows user/feedback/reference/project.

Memory types:
  user      — who the person is (identity, background, goals, knowledge)
  feedback  — how LangBridge should behave (corrections and working rules)
  project   — project context not recoverable from code (why, decisions, owners)
  reference — where external information lives (not the external data itself)

Both scopes have their own memory.md index and memory/ entry directory; both
indexes are always considered during prefetch.

Agents invoke a tool-using Memory Writer fork as soon as durable information
appears. The fork reuses the live context (prefix cache), reads both indexes,
and uses ordinary file tools in a restricted staged workspace to add, edit, or
delete entries. A background run at phase/turn end catches anything missed —
and exits with no file changes when nothing durable is worth saving.
"""
from __future__ import annotations

import re
import threading
import json
import shutil
import tempfile
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path

from langbridge_code.settings import PROJECT_MEMORY_PATH, USER_MEMORY_PATH

SCOPE_PROJECT = "project"
SCOPE_USER = "user"
SCOPES = (SCOPE_USER, SCOPE_PROJECT)
TYPE_USER = "user"
TYPE_FEEDBACK = "feedback"
TYPE_PROJECT = "project"
TYPE_REFERENCE = "reference"
MEMORY_TYPES = (TYPE_USER, TYPE_FEEDBACK, TYPE_PROJECT, TYPE_REFERENCE)
SCOPE_MEMORY_TYPES = {
    SCOPE_USER: (TYPE_USER, TYPE_FEEDBACK, TYPE_REFERENCE),
    SCOPE_PROJECT: (TYPE_USER, TYPE_FEEDBACK, TYPE_REFERENCE, TYPE_PROJECT),
}

MAX_ENTRY_CHARS = 8_000
MAX_BLOCK_CHARS = 24_000

_MEMORY_REF_RE = re.compile(r"\b(user|project)/([\w.\-]+\.md)\b")
_memory_lock = threading.RLock()
_memory_writer_lock = threading.Lock()


@dataclass
class MemoryEntry:
    scope: str
    filename: str
    name: str
    description: str
    memory_type: str
    content: str


def scope_for_type(memory_type: str) -> str:
    """Legacy default scope for callers that do not provide one explicitly."""
    if memory_type in {TYPE_USER, TYPE_FEEDBACK}:
        return SCOPE_USER
    if memory_type in {TYPE_PROJECT, TYPE_REFERENCE}:
        return SCOPE_PROJECT
    raise ValueError(f"Unknown memory type: {memory_type}")


def valid_scope_type(scope: str, memory_type: str) -> bool:
    return memory_type in SCOPE_MEMORY_TYPES.get(scope, ())


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
    """Read and combine BOTH user and project memory.md indexes."""
    parts = []
    for scope in SCOPES:
        index = read_memory_index(scope)
        if index:
            parts.append(f"## {scope}\n{index}")
    return "\n\n".join(parts)


def _slugify(title: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", (title or "").strip().lower()).strip("-")
    return (slug[:max_len].strip("-")) or "memory"


def _frontmatter_value(value: str) -> str:
    return json.dumps(" ".join((value or "").split()), ensure_ascii=False)


def render_memory_entry(
    name: str,
    description: str,
    memory_type: str,
    content: str,
) -> str:
    return (
        "---\n"
        f"name: {_frontmatter_value(name)}\n"
        f"description: {_frontmatter_value(description)}\n"
        f"type: {memory_type}\n"
        "---\n"
        f"{content.strip()}\n"
    )


def _parse_scalar(value: str) -> str:
    value = value.strip()
    try:
        parsed = json.loads(value)
        return str(parsed) if isinstance(parsed, str) else value
    except (json.JSONDecodeError, ValueError):
        return value.strip("\"'")


def parse_memory_entry(scope: str, filename: str, text: str) -> MemoryEntry:
    """Parse the YAML-frontmatter format, with a legacy markdown fallback."""
    raw = (text or "").strip()
    if raw.startswith("---\n") and "\n---" in raw[4:]:
        frontmatter, content = raw[4:].split("\n---", 1)
        fields = {}
        for line in frontmatter.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = _parse_scalar(value)
        memory_type = fields.get(
            "type", TYPE_USER if scope == SCOPE_USER else TYPE_PROJECT
        )
        return MemoryEntry(
            scope=scope,
            filename=filename,
            name=fields.get("name") or Path(filename).stem,
            description=fields.get("description") or fields.get("name") or Path(filename).stem,
            memory_type=memory_type,
            content=content.strip(),
        )

    # Old entries were "# title\n\nbody". They remain readable and are
    # converted to frontmatter the next time the topic is updated.
    lines = raw.splitlines()
    title = lines[0].lstrip("# ").strip() if lines else Path(filename).stem
    content = "\n".join(lines[1:]).strip() if len(lines) > 1 else raw
    return MemoryEntry(
        scope=scope,
        filename=filename,
        name=title or Path(filename).stem,
        description=title or Path(filename).stem,
        memory_type=TYPE_USER if scope == SCOPE_USER else TYPE_PROJECT,
        content=content,
    )


def list_memory_entries(scope: str) -> list[MemoryEntry]:
    directory = memory_dir(scope)
    if not directory.is_dir():
        return []
    entries = []
    for path in sorted(directory.glob("*.md")):
        entries.append(parse_memory_entry(scope, path.name, path.read_text(encoding="utf-8")))
    return entries


def _rebuild_memory_index(scope: str) -> None:
    entries = list_memory_entries(scope)
    lines = [
        f"- [{entry.memory_type}] {entry.filename}: {entry.description}"
        for entry in entries
    ]
    index_path = memory_index_path(scope)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def _similarity_text(*values: str) -> str:
    return re.sub(r"[\W_]+", "", " ".join(values).lower(), flags=re.UNICODE)


def _character_ngrams(text: str, size: int = 2) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _deterministic_duplicate(
    entries: list[MemoryEntry],
    *,
    memory_type: str,
    name: str,
    description: str,
    content: str,
) -> MemoryEntry | None:
    target = _similarity_text(name, description, content)
    target_name = _slugify(name)
    target_grams = _character_ngrams(target)
    best = None
    best_score = 0.0
    for entry in entries:
        if entry.memory_type != memory_type:
            continue
        if _slugify(entry.name) == target_name:
            return entry
        candidate = _similarity_text(entry.name, entry.description, entry.content)
        candidate_grams = _character_ngrams(candidate)
        union = target_grams | candidate_grams
        jaccard = len(target_grams & candidate_grams) / len(union) if union else 0.0
        sequence = SequenceMatcher(None, target, candidate).ratio()
        score = max(jaccard, sequence)
        if score > best_score:
            best, best_score = entry, score
    return best if best_score >= 0.72 else None


DEDUPE_SYSTEM = """You deduplicate durable memory entries.
Return exactly NEW, or the filename of ONE existing entry that describes the
same underlying fact/rule/topic as the candidate. Treat a correction or
contradiction about the same topic as a match so the stale entry is replaced.
Do not match merely because entries share broad words. Compare semantics."""


def _llm_duplicate(
    entries: list[MemoryEntry],
    *,
    api_key,
    model,
    memory_type: str,
    name: str,
    description: str,
    content: str,
) -> MemoryEntry | None:
    candidates = [entry for entry in entries if entry.memory_type == memory_type]
    if not candidates or not (api_key and model):
        return None
    try:
        from langbridge_code.llm.client import create_model_response
        from langbridge_code.llm.parse import extract_output_text, truncate_text

        existing = "\n\n".join(
            f"FILE: {entry.filename}\nNAME: {entry.name}\n"
            f"DESCRIPTION: {entry.description}\nCONTENT: {truncate_text(entry.content, 1200)}"
            for entry in candidates
        )
        candidate = (
            f"TYPE: {memory_type}\nNAME: {name}\nDESCRIPTION: {description}\n"
            f"CONTENT: {truncate_text(content, 1600)}"
        )
        response = create_model_response(
            api_key,
            model,
            [
                {"role": "system", "content": DEDUPE_SYSTEM},
                {
                    "role": "user",
                    "content": f"Existing entries:\n{existing}\n\nCandidate:\n{candidate}",
                },
            ],
            label="memory dedupe",
        )
        answer = extract_output_text(response.get("output", [])).strip()
    except Exception:
        return None
    for entry in candidates:
        if answer == entry.filename:
            return entry
    return None


def write_memory(
    memory_type: str,
    name: str,
    description: str,
    content: str,
    *,
    scope=None,
    api_key=None,
    model=None,
) -> str:
    """Write one typed memory, semantically replacing duplicates/conflicts."""
    if memory_type not in MEMORY_TYPES:
        return (
            f"Unknown memory type '{memory_type}'. Use "
            "user, feedback, project, or reference."
        )
    name = " ".join((name or "").split()).strip()
    description = " ".join((description or "").split()).strip()
    body = (content or "").strip()
    if not name or not description or not body:
        return "Memory needs name, description, and content; nothing saved."
    scope = scope or scope_for_type(memory_type)
    if not valid_scope_type(scope, memory_type):
        return f"Memory type '{memory_type}' is not valid in scope '{scope}'."

    with _memory_writer_lock, _memory_lock:
        directory = memory_dir(scope)
        directory.mkdir(parents=True, exist_ok=True)
        entries = list_memory_entries(scope)
        duplicate = _deterministic_duplicate(
            entries,
            memory_type=memory_type,
            name=name,
            description=description,
            content=body,
        )
        if duplicate is None:
            duplicate = _llm_duplicate(
                entries,
                api_key=api_key,
                model=model,
                memory_type=memory_type,
                name=name,
                description=description,
                content=body,
            )
        filename = duplicate.filename if duplicate else f"{_slugify(name)}.md"
        canonical_name = duplicate.name if duplicate else name
        (directory / filename).write_text(
            render_memory_entry(canonical_name, description, memory_type, body),
            encoding="utf-8",
        )
        _rebuild_memory_index(scope)
    action = "Updated" if duplicate else "Saved"
    return (
        f"{action} {memory_type} memory: {scope}/{filename} — "
        f"{canonical_name}"
    )


def read_memory_entry(scope: str, name: str) -> str:
    if scope not in SCOPES or "/" in name or ".." in name:
        return ""
    path = memory_dir(scope) / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


PREFETCH_SYSTEM = """You select which memory files are relevant to a task.

You always receive BOTH memory.md indexes:
- user scope is global and may contain user, feedback, or reference entries.
- project scope is repository-specific and may contain user, feedback,
  reference, or project entries.

Reply with one file reference per line in the form user/<file>.md or
project/<file>.md. Pick only files likely to help with THIS task. If nothing
is relevant, reply exactly NONE."""


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


MEMORY_WRITER_INSTRUCTION = """You are the Memory Writer fork. Maintain durable long-term
memory using the ordinary file tools available to you, then exit.

Your restricted workspace contains exactly two scopes:
- `user/memory.md` and `user/memory/*.md`: memories useful across projects.
- `project/memory.md` and `project/memory/*.md`: memories only for this project.

Scope and type are independent:
- user scope allows type `user`, `feedback`, or `reference`.
- project scope allows type `user`, `feedback`, `reference`, or `project`.
- `user` describes the human's identity, background, goals, role, or knowledge.
- `feedback` describes how LangBridge should work or respond.
- `reference` records where durable external information lives, not the data itself.
- `project` records project rationale, decisions, ownership, deadlines, or context
  that cannot be recovered from code or git.

First read both `memory.md` indexes. Read candidate entry files before changing or
deleting them. Use this live conversation as evidence and autonomously:
- add durable information that will matter in later sessions;
- update entries that are incomplete, stale, inaccurate, or superseded;
- delete entries that are outdated, inaccurate, conflicting, duplicated, based on
  an assistant guess, or no longer worth retaining.

Do not treat the assistant's unsupported inference as a user fact. Do not store task
status, code structure, file paths recoverable from the repo, or transient details.
Prefer one canonical entry per topic. Keep descriptions concise.

Each entry must be a markdown file with this exact frontmatter:
---
name: "stable-lowercase-kebab-name"
description: "one concise index sentence"
type: user|feedback|reference|project
---
<durable markdown body>

Create, edit, and delete only files under `user/` and `project/`. To delete a
file, use bash (`rm path`). The indexes are rebuilt after you finish, so focus
on entry files. If nothing durable is worth adding, updating, or deleting, make
no file changes. When done, reply with a brief summary and stop."""

MEMORY_FILE_TOOL_NAMES = {
    "read_file",
    "write",
    "Edit",
    "bash",
}


def _stage_memory_workspace(root: Path) -> None:
    for scope in SCOPES:
        staged_scope = root / scope
        staged_directory = staged_scope / "memory"
        staged_directory.mkdir(parents=True, exist_ok=True)
        index_path = memory_index_path(scope)
        if index_path.is_file():
            shutil.copy2(index_path, staged_scope / "memory.md")
        else:
            (staged_scope / "memory.md").write_text("", encoding="utf-8")
        source_directory = memory_dir(scope)
        if source_directory.is_dir():
            for source in source_directory.glob("*.md"):
                shutil.copy2(source, staged_directory / source.name)


def _validate_staged_memories(root: Path) -> None:
    for scope in SCOPES:
        for path in (root / scope / "memory").glob("*.md"):
            entry = parse_memory_entry(scope, path.name, path.read_text(encoding="utf-8"))
            if not valid_scope_type(scope, entry.memory_type):
                raise ValueError(
                    f"Memory {scope}/{path.name} has invalid type {entry.memory_type!r}."
                )
            if not entry.name.strip() or not entry.description.strip() or not entry.content.strip():
                raise ValueError(f"Memory {scope}/{path.name} is missing required content.")


def _sync_staged_memories(root: Path) -> None:
    _validate_staged_memories(root)
    with _memory_lock:
        for scope in SCOPES:
            source_directory = root / scope / "memory"
            destination_directory = memory_dir(scope)
            destination_directory.mkdir(parents=True, exist_ok=True)
            staged_names = {path.name for path in source_directory.glob("*.md")}
            for existing in destination_directory.glob("*.md"):
                if existing.name not in staged_names:
                    existing.unlink()
            for source in source_directory.glob("*.md"):
                shutil.copy2(source, destination_directory / source.name)
            _rebuild_memory_index(scope)


def run_memory_writer_agent(api_key, model, messages) -> str:
    """Run a prefix-cache-friendly, tool-using Memory Writer fork."""
    from langbridge_code.agents.common.fork import fork_agent
    from langbridge_code.agents.common.workspace import workspace_scope
    from langbridge_code.tools import execution, filesystem

    available_schemas = filesystem.TOOL_SCHEMAS + execution.TOOL_SCHEMAS
    available_tools = filesystem.TOOLS | execution.TOOLS
    schemas = [
        schema
        for schema in available_schemas
        if schema["name"] in MEMORY_FILE_TOOL_NAMES
    ]
    tools = {name: available_tools[name] for name in MEMORY_FILE_TOOL_NAMES}
    with _memory_writer_lock:
        with tempfile.TemporaryDirectory(prefix="langbridge-memory-") as temporary:
            root = Path(temporary)
            _stage_memory_workspace(root)
            with workspace_scope(root):
                report = fork_agent(
                    api_key,
                    model,
                    list(messages),
                    MEMORY_WRITER_INSTRUCTION,
                    tool_schemas=schemas,
                    tools=tools,
                    label="Memory Writer",
                )
            _sync_staged_memories(root)
    return report or "Memory Writer finished."


def schedule_memory_writer(api_key, model, messages) -> None:
    """Run the tool-using Memory Writer fork in a background thread."""
    if not (api_key and model and messages):
        return
    snapshot = list(messages)

    def worker() -> None:
        try:
            run_memory_writer_agent(api_key, model, snapshot)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True, name="memory-writer").start()
