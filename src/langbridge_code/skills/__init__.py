import re
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

# Expertise playbooks only — general guidance lives in each agent's system prompt.
# Explorer debugging guidance is inlined in EXPLORER_PROMPT (no role playbooks).
# Karpathy think-before-coding / surgical-changes are inlined in WORKER_CODING_GENERAL.
EXPLORER_SKILL_NAMES: tuple[str, ...] = ()

PLANNER_SKILL_NAMES = (
    "superpowers_brainstorming",
    "superpowers_writing-plans",
)

WORKER_CODING_SKILL_NAMES = (
    "superpowers_test-driven-development",
    "superpowers_systematic-debugging",
    "superpowers_receiving-code-review",
)

REVIEWER_CODING_SKILL_NAMES = (
    "clean-code-guard",
    "test-guard",
    "docs-guard",
    "wp-guard",
    "woo-guard",
)

LANGBRIDGE_SKILL_NAMES = ("grilling", "writing-simple-plans")


def langbridge_skill_catalog():
    return skill_catalog_text_for(LANGBRIDGE_SKILL_NAMES)


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


def load_skill(name):
    """Return a skill playbook or a file under that skill directory.

    Names:
      - ``clean-code-guard`` → that skill's ``SKILL.md`` (frontmatter stripped)
      - ``clean-code-guard/references/ai-failure-modes.md`` → that reference file
    """
    name = name.strip().strip("/")
    if not name or ".." in Path(name).parts:
        raise FileNotFoundError(name)

    for root in _skill_dirs():
        # Progressive disclosure: skill/references/foo.md
        if "/" in name:
            target = (root / name).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                continue
            if target.is_file():
                text = target.read_text(encoding="utf-8")
                return _strip_frontmatter(text).strip() if target.name == "SKILL.md" else text.strip()
            continue

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
    allowed = set(skill_names)
    lookup = dict(list_skills())
    return "\n".join(
        f"- {name}: {lookup[name]}"
        for name in skill_names
        if name in lookup
    )


def skill_catalog_text_for_roles(roles):
    """Catalog from multiple skill role directories."""
    seen = set()
    lines = []
    for role in roles:
        for name, description in list_skills(role=role):
            if name in seen:
                continue
            seen.add(name)
            lines.append(f"- {name}: {description}")
    return "\n".join(lines)


def worker_skill_catalog(task_type="coding"):
    return skill_catalog_text_for(WORKER_CODING_SKILL_NAMES)


def reviewer_skill_catalog(task_type="coding"):
    return skill_catalog_text_for(REVIEWER_CODING_SKILL_NAMES)


SKILL_SELECT_SYSTEM = """You select which skills might help with a task.

You get a skill index ("- name: description" lines) and the current task.
Reply with one skill name per line — only names from the index that are
plausibly relevant to THIS task. If none apply, reply exactly NONE."""


def select_skill_index(api_key, model, task: str, catalog: str, *, label: str = "skill prefetch") -> str:
    """One-pass LLM pick of likely-relevant skill index lines from a role catalog.

    Falls back to the full catalog when there is no API access or the call fails
    — the index lines are cheap and read_skill loads bodies on demand.
    """
    catalog = (catalog or "").strip()
    if not catalog:
        return ""
    if not (api_key and model):
        return catalog
    try:
        from langbridge_code.llm.client import create_model_response
        from langbridge_code.llm.parse import extract_output_text, truncate_text

        data = create_model_response(
            api_key,
            model,
            [
                {"role": "system", "content": SKILL_SELECT_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Skill index:\n{catalog}\n\n"
                        f"Task:\n{truncate_text((task or '').strip(), 4_000)}"
                    ),
                },
            ],
            label=label,
        )
        reply = extract_output_text(data.get("output", [])).strip()
    except Exception:
        return catalog
    if not reply:
        return catalog
    if reply.upper().startswith("NONE"):
        return ""
    picked = {line.strip().strip("-").strip() for line in reply.splitlines() if line.strip()}
    selected = [
        line
        for line in catalog.splitlines()
        if line.strip().startswith("- ") and line.strip()[2:].split(":", 1)[0].strip() in picked
    ]
    return "\n".join(selected) if selected else catalog


def ensure_skill_index_block(stack, api_key, model, task, catalog, *, label="skill prefetch"):
    """Set the <skill_index> context block once per session (idempotent)."""
    if stack.skill_index_block or not (catalog or "").strip():
        return
    stack.set_skill_index_block(select_skill_index(api_key, model, task, catalog, label=label))


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


def format_skill_slash_turn(name: str, body: str, args: str = "") -> str:
    """Build the user-turn content for a slash-invoked skill."""
    filled = substitute_arguments(body, args if args else "")
    return (
        f"The user invoked the /{name} skill via slash command. Follow this skill now.\n\n"
        f'<skill name="{name}">\n{filled}\n</skill>'
    )


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
        body = load_skill(name)
    except FileNotFoundError:
        return "unknown", name
    return "expanded", format_skill_slash_turn(name, body, args)


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
