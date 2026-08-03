import re
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent

_ARGUMENTS_INDEX_RE = re.compile(r"\$ARGUMENTS\[(\d+)\]")
_SHORTHAND_INDEX_RE = re.compile(r"\$(\d+)(?!\w)")

AGENT_ROLES = (
    "langbridge",
    "explorer",
    "planner",
    "worker",
    "worker_coder",
    "reviewer_code",
)


def normalize_task_type(task_type):
    """Only coding remains; legacy slide/presentation values coerce to coding."""
    return "coding"


def _agent_skill_dirs():
    """Per-agent skill roots (skills/<role>/)."""
    for role in AGENT_ROLES:
        path = SKILLS_DIR / role
        if path.is_dir():
            yield path


def _skill_dirs():
    """Search roots for skills, in priority order."""
    yield from _agent_skill_dirs()


def load_skill(name, *, role=None):
    """Return a skill playbook or a file under that skill directory.

    Names:
      - ``clean-code-guard`` → that skill's ``SKILL.md`` (frontmatter stripped)
      - ``clean-code-guard/references/ai-failure-modes.md`` → that reference file

    When ``role`` is set, only ``skills/<role>/`` is searched (used by
    ``read_skill`` so agents cannot load another role's playbooks). With
    ``role=None``, all agent skill dirs are searched — used by tests.
    """
    name = name.strip().strip("/")
    if not name or ".." in Path(name).parts:
        raise FileNotFoundError(name)

    roots = [SKILLS_DIR / role] if role is not None else list(_skill_dirs())

    for root in roots:
        if not root.is_dir():
            continue
        # Progressive disclosure: skill/references/foo.md
        # Resolve may land under skills/_external via a role-dir symlink; allow
        # any path still inside SKILLS_DIR (block escapes outside the package).
        if "/" in name:
            target = (root / name).resolve()
            try:
                target.relative_to(SKILLS_DIR.resolve())
            except ValueError:
                continue
            if not target.is_file():
                continue
            # Prefer hits that belong to this role root (direct or symlink).
            role_anchor = (root / name.split("/", 1)[0]).resolve()
            try:
                target.relative_to(role_anchor)
            except ValueError:
                continue
            text = target.read_text(encoding="utf-8")
            return _strip_frontmatter(text).strip() if target.name == "SKILL.md" else text.strip()

        skill_md = root / name / "SKILL.md"
        if skill_md.exists():
            return _strip_frontmatter(skill_md.read_text(encoding="utf-8")).strip()
    raise FileNotFoundError(name)


def list_skills(role=None, roles=None):
    """Return [(name, description), ...] for skills under agent folders."""
    if roles is not None:
        roots = [SKILLS_DIR / role_name for role_name in roles]
    elif role is not None:
        roots = [SKILLS_DIR / role]
    else:
        roots = list(_agent_skill_dirs())

    skills = []
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            skill_md = path / "SKILL.md"
            if path.is_dir() and skill_md.exists() and path.name not in seen:
                meta = _frontmatter(skill_md.read_text(encoding="utf-8"))
                skills.append((path.name, meta.get("description", "")))
                seen.add(path.name)
    return skills


def skill_catalog_text():
    """One '- name: description' line per skill, for prompt injection."""
    return "\n".join(f"- {name}: {description}" for name, description in list_skills())


def skill_catalog_text_for(skill_names):
    """Catalog lines for a subset of skills (unknown names are skipped)."""
    lookup = dict(list_skills())
    return "\n".join(
        f"- {name}: {lookup[name]}"
        for name in skill_names
        if name in lookup
    )


def skill_catalog_text_for_roles(roles):
    """Catalog from role skill directories on disk (skills/<role>/)."""
    seen = set()
    lines = []
    for role in roles:
        for name, description in list_skills(role=role):
            if name in seen:
                continue
            seen.add(name)
            lines.append(f"- {name}: {description}")
    return "\n".join(lines)


def skill_catalog_for_role(role: str) -> str:
    """Build <skill_index> text from whatever lives under skills/<role>/."""
    return skill_catalog_text_for_roles([role])


def langbridge_skill_catalog():
    return skill_catalog_for_role("langbridge")


def planner_skill_catalog():
    return skill_catalog_for_role("planner")


def explorer_skill_catalog():
    return skill_catalog_for_role("explorer")


def worker_skill_catalog(task_type="coding"):
    normalize_task_type(task_type)
    return skill_catalog_for_role("worker_coder")


def reviewer_skill_catalog(task_type="coding"):
    normalize_task_type(task_type)
    return skill_catalog_for_role("reviewer_code")


# Claude Code alignment: full listing at start; after compaction only re-inject
# invoked skill bodies (most recent first), with per-skill and total budgets.
INVOKED_SKILL_MAX_TOKENS = 5_000
INVOKED_SKILLS_TOTAL_TOKENS = 25_000


def canonical_skill_name(name: str) -> str:
    """Top-level skill id (``clean-code-guard/references/x`` → ``clean-code-guard``)."""
    return (name or "").strip().strip("/").split("/", 1)[0]


def ensure_skill_index_block(stack, api_key, model, task, catalog, *, label="skill prefetch"):
    """Pin the full skill listing once (no LLM filter). Idempotent.

    After compaction the listing is cleared and not restored — only invoked
    skill bodies come back (see ``refresh_skills_after_compact``).
    """
    del api_key, model, task, label
    _ensure_skills_compact_hook(stack)
    if getattr(stack, "skills_listing_cleared", False):
        return
    if stack.skill_index_block or not (catalog or "").strip():
        return
    stack.set_skill_index_block(catalog.strip())


def _ensure_skills_compact_hook(stack) -> None:
    if getattr(stack, "_skills_compact_hook_ready", False):
        return
    previous = stack.on_compacted

    def on_compacted(compacted_stack):
        if previous is not None:
            try:
                previous(compacted_stack)
            except Exception:
                pass
        refresh_skills_after_compact(compacted_stack)

    stack.on_compacted = on_compacted
    stack._skills_compact_hook_ready = True


def record_invoked_skill(stack, name: str, *, role=None, body: str | None = None) -> None:
    """Remember a skill so its body can be re-injected after compaction.

    When ``body`` is omitted, loads the canonical ``SKILL.md``. Callers that
    already have content (slash expand, ``read_skill`` wrapper) should pass it.
    """
    skill_name = canonical_skill_name(name)
    if not skill_name or not hasattr(stack, "record_invoked_skill"):
        return
    if body is not None:
        text = str(body).strip()
    else:
        try:
            text = (load_skill(skill_name, role=role) or "").strip()
        except FileNotFoundError:
            return
    if not text or text.startswith("Tool error:"):
        return
    stack.record_invoked_skill(skill_name, text)


def attach_skill_tracking(stack, tools: dict, *, role=None) -> dict:
    """Wrap ``read_skill`` so successful loads are recorded on ``stack``."""
    original = tools.get("read_skill")
    if original is None or getattr(original, "_tracks_invoked_skills", False):
        return tools

    def read_skill(name, **kwargs):
        output = original(name, **kwargs)
        if output is not None and not str(output).startswith("Tool error:"):
            # Re-inject the playbook (SKILL.md), not a one-off reference file.
            playbook = None
            try:
                playbook = load_skill(canonical_skill_name(name), role=role)
            except FileNotFoundError:
                playbook = str(output)
            record_invoked_skill(stack, name, role=role, body=playbook)
        return output

    read_skill._tracks_invoked_skills = True  # type: ignore[attr-defined]
    tools["read_skill"] = read_skill
    return tools


def format_invoked_skills_block(
    invoked: list[dict],
    *,
    per_skill_tokens: int = INVOKED_SKILL_MAX_TOKENS,
    total_tokens: int = INVOKED_SKILLS_TOTAL_TOKENS,
) -> str:
    """Build the post-compaction ``<invoked_skills>`` body (most recent first)."""
    from langbridge_code.context.common.budget import estimate_tokens

    sections: list[str] = []
    used = 0
    # Most recently invoked first (list is oldest→newest).
    for entry in reversed(list(invoked or [])):
        name = str(entry.get("name") or "").strip()
        body = str(entry.get("body") or "").strip()
        if not name or not body:
            continue
        budget = min(per_skill_tokens, max(0, total_tokens - used))
        if budget <= 0:
            break
        # Rough char budget from token estimate helper (json dumps // 4).
        max_chars = max(1, budget * 4)
        clipped = body if len(body) <= max_chars else body[:max_chars].rstrip() + "\n…"
        section = f'## {name}\n{clipped}'
        cost = estimate_tokens(section)
        if used + cost > total_tokens and sections:
            break
        sections.append(section)
        used += cost
    if not sections:
        return ""
    header = (
        "The following skills were invoked earlier in this session. "
        "Continue to follow these guidelines:"
    )
    return header + "\n\n" + "\n\n".join(sections)


def refresh_skills_after_compact(stack) -> None:
    """Drop the skill listing; re-pin invoked skill bodies under the token budget."""
    stack.skills_listing_cleared = True
    stack.set_skill_index_block(None)
    block = format_invoked_skills_block(getattr(stack, "invoked_skills", []) or [])
    if hasattr(stack, "set_invoked_skills_block"):
        stack.set_invoked_skills_block(block or None)


def _frontmatter(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    meta = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta


def _strip_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + len("\n---") :].lstrip("\n")
    return text


# TUI-local slash commands — never treated as skill invokes if they reach Python.
RESERVED_SLASH_COMMANDS = frozenset(
    {
        "exit",
        "quit",
        "help",
        "copy",
        "new",
        "sessions",
        "resume",
        "delete",
        "approve",
        "yolo",
        "deny",
        "pause",
        "stop",
        "queue",
        "goal",
        "banner",
    }
)


def parse_skill_slash(text: str):
    """Parse ``/skill-name args`` into ``(name, args)``, or ``None``.

    Reserved TUI commands return ``None`` so they are not treated as skills.
    """
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    first, _, rest = text[1:].partition(" ")
    name = first.strip()
    if not name or "/" in name or ".." in name:
        return None
    if name.lower() in RESERVED_SLASH_COMMANDS:
        return None
    return name, rest.strip()


def substitute_arguments(content: str, args: str | None, *, append_if_no_placeholder: bool = True) -> str:
    """Replace ``$ARGUMENTS`` / ``$ARGUMENTS[n]`` / ``$n`` like Claude Code.

    If there are no placeholders and ``args`` is non-empty, append
    ``ARGUMENTS: ...`` so the model still sees the user input.
    """
    if args is None:
        return content
    parsed = [part for part in args.split() if part] if args.strip() else []
    original = content

    def indexed(match):
        index = int(match.group(1))
        return parsed[index] if index < len(parsed) else ""

    content = _ARGUMENTS_INDEX_RE.sub(indexed, content)
    content = _SHORTHAND_INDEX_RE.sub(indexed, content)
    content = content.replace("$ARGUMENTS", args)
    if content == original and append_if_no_placeholder and args:
        content = f"{content}\n\nARGUMENTS: {args}"
    return content


def resolve_skill_dir(name: str, *, role: str = "langbridge") -> Path | None:
    """Absolute skill directory for ``name`` under ``skills/<role>/``, if present."""
    skill_name = (name or "").strip().strip("/").split("/", 1)[0]
    if not skill_name or ".." in skill_name or "/" in skill_name:
        return None
    path = SKILLS_DIR / role / skill_name
    if (path / "SKILL.md").exists():
        return path.resolve()
    return None


def format_skill_runtime_env(name: str, *, role: str = "langbridge") -> str:
    """Concrete SKILL_ROOT + PYTHON for slash turns (no agent rediscovery)."""
    root = resolve_skill_dir(name, role=role)
    if root is None:
        return ""
    return (
        "## LangBridge runtime (injected — do not rediscover)\n\n"
        f"- `SKILL_ROOT={root}`\n"
        f"- `PYTHON={sys.executable}`\n"
        "- Run skill scripts as: "
        "`\"$PYTHON\" \"$SKILL_ROOT/scripts/<script>.py\" ...`\n"
        "- Workspace cwd is usually a different project; do not assume it is "
        "the skill root.\n"
        "- Do not use bare `python3`, and do not search for the skill directory.\n"
    )


def format_skill_slash_turn(
    name: str, body: str, args: str = "", *, role: str = "langbridge"
) -> str:
    """Build the user-turn content for a slash-invoked skill."""
    filled = substitute_arguments(body, args if args else "")
    runtime = format_skill_runtime_env(name, role=role)
    header = (
        f"The user invoked the /{name} skill via slash command. Follow this skill now."
    )
    if runtime:
        header = f"{header}\n\n{runtime.rstrip()}"
    return f"{header}\n\n<skill name=\"{name}\">\n{filled}\n</skill>"


def resolve_skill_slash(text: str):
    """Resolve a possible skill slash invoke.

    Returns ``(status, payload)``:
      - ``("passthrough", text)`` — not a skill slash; use text as-is
      - ``("expanded", content)`` — known skill; use expanded turn content
      - ``("unknown", name)`` — slash that is neither reserved nor a skill
    """
    text = (text or "").strip()
    parsed = parse_skill_slash(text)
    if parsed is None:
        return "passthrough", text
    name, args = parsed
    try:
        body = load_skill(name, role="langbridge")
    except FileNotFoundError:
        return "unknown", name
    return "expanded", format_skill_slash_turn(name, body, args, role="langbridge")


def expand_skill_slash(text: str) -> str:
    """Expand a skill slash into turn content, or return ``text`` unchanged.

    Raises ``FileNotFoundError`` for unknown non-reserved slash commands.
    """
    status, payload = resolve_skill_slash(text)
    if status == "passthrough":
        return payload
    if status == "unknown":
        raise FileNotFoundError(payload)
    return payload
