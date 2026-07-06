from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent


def _skill_dirs():
    """Search roots for skills, in priority order.

    Built-in package skills first, then the evolver-written skills under the
    policy dir (POLICY_DIR/skills). Keeping the evolved skills outside the package
    lets a checkpoint stay self-contained and lets read_skill pick them up without
    mutating installed source.
    """
    dirs = [SKILLS_DIR]
    try:
        from langbridge_cli import policy

        extra = Path(policy.skills_dir())
        if extra.is_dir() and extra.resolve() != SKILLS_DIR.resolve():
            dirs.append(extra)
    except Exception:
        pass
    return dirs


def load_skill(name):
    """Return the body of a skill's SKILL.md (YAML frontmatter stripped)."""
    for root in _skill_dirs():
        skill_md = root / name / "SKILL.md"
        if skill_md.exists():
            return _strip_frontmatter(skill_md.read_text(encoding="utf-8")).strip()
    raise FileNotFoundError(name)


def list_skills():
    """Return [(name, description), ...] for every skill folder with a SKILL.md.

    The folder name is the skill's id (what load_skill / read_skill take); the
    description comes from the SKILL.md frontmatter. Built-in skills win over an
    evolver skill with the same id.
    """
    skills = []
    seen = set()
    for root in _skill_dirs():
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
