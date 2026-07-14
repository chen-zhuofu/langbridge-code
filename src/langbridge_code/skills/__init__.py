from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent

# Upstream Superpowers vendor tree — not loaded at runtime; agent folders hold copies.
SUPERPOWERS_VENDOR_DIR = SKILLS_DIR / "superpowers"

AGENT_ROLES = (
    "langbridge",
    "explorer",
    "planner",
    "worker",
    "worker_coder",
    "worker_presenter",
    "reviewer_code",
    "reviewer_slide",
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

WORKER_SLIDE_SKILL_NAMES: tuple[str, ...] = ()

REVIEWER_CODING_SKILL_NAMES = (
    "clean-code-guard",
    "test-guard",
    "docs-guard",
    "wp-guard",
    "woo-guard",
)

REVIEWER_SLIDE_SKILL_NAMES: tuple[str, ...] = ()

# Legacy aliases for tests and training checkpoints.
LANGBRIDGE_SKILL_NAMES = ("grilling", "writing-simple-plans")
CODER_SKILL_NAMES = WORKER_CODING_SKILL_NAMES
REVIEWER_SKILL_NAMES = REVIEWER_CODING_SKILL_NAMES


def langbridge_skill_catalog():
    return skill_catalog_text_for(LANGBRIDGE_SKILL_NAMES)


def normalize_task_type(task_type):
    if task_type in ("presentation", "slide"):
        return "slide"
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
    names = WORKER_SLIDE_SKILL_NAMES if normalize_task_type(task_type) == "slide" else WORKER_CODING_SKILL_NAMES
    return skill_catalog_text_for(names)


def reviewer_skill_catalog(task_type="coding"):
    names = REVIEWER_SLIDE_SKILL_NAMES if normalize_task_type(task_type) == "slide" else REVIEWER_CODING_SKILL_NAMES
    return skill_catalog_text_for(names)


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
