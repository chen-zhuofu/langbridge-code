"""policy.py — the mutable agent policy the evolver writes and the agents read.

This is the shared state that lets the outer evolver loop improve PM / L4 / L5 / L3
WITHOUT editing their code. Each agent run folds the current policy into its system
prompt; the evolver appends to it after looking at a batch of traces.

Two levers (a deliberate subset of what the neighbour's coder/reviewer evolver
used, mapped onto this repo's four roles):

  1. guidance — bullet rules appended to a role's system prompt (workflow fixes
     and cautions distilled from past runs).
  2. skills   — markdown playbooks written into POLICY_DIR/skills. They are picked
     up by the existing read_skill tool (langbridge_cli.skills also scans the
     policy skills dir) and an index is injected into the relevant role's prompt.

Layout (POLICY_DIR, default <repo>/training/policy, override with LANGBRIDGE_POLICY_DIR):

  policy.json          {version, pm:{guidance}, l4:{...}, l5:{...}, l3:{...},
                        skills:[{id,name,when,target}], history:[...]}
  skills/<id>/SKILL.md  one skill playbook (with frontmatter description)
  checkpoints/<label>/  a self-contained snapshot (policy.json + skills/ + meta.json)

Guidance is deduped and capped (MAX_GUIDANCE per role) so prompts can't grow
without bound — a guard against prompt bloat / mode collapse. The evolver can also
remove/replace bullets, so it can refine instead of only stacking.

This module imports nothing from langbridge_cli.agents, so it is safe to import
from the role/prompt-building code without a cycle.
"""
import datetime
import json
import os
import re
import shutil
from pathlib import Path

# The four roles in this repo's loop-engineering architecture.
ROLES = ("pm", "l4", "l5", "l3")
# Skill targets the evolver may use; expanded to concrete roles by skills_for().
_TARGET_ALIASES = {
    "implementer": ("l4", "l5"),
    "engineer": ("l4", "l5"),
    "both": ("l4", "l5"),
    "all": ROLES,
}

MAX_GUIDANCE = int(os.environ.get("LANGBRIDGE_MAX_GUIDANCE", "12"))


def _default_policy_dir() -> str:
    env = os.environ.get("LANGBRIDGE_POLICY_DIR")
    if env:
        return os.path.abspath(env)
    # <repo>/training/policy — stable regardless of the agent's working directory
    # (agents cd into target repos during eval, so we cannot use cwd here).
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "training" / "policy")


def policy_dir() -> str:
    return _default_policy_dir()


def policy_file() -> str:
    return os.path.join(policy_dir(), "policy.json")


def skills_dir() -> str:
    return os.path.join(policy_dir(), "skills")


def checkpoints_dir() -> str:
    return os.path.join(policy_dir(), "checkpoints")


def _default():
    base = {"version": 0, "skills": [], "history": []}
    for role in ROLES:
        base[role] = {"guidance": []}
    return base


def load():
    """Return the current policy dict (missing keys defaulted)."""
    path = policy_file()
    if not os.path.exists(path):
        return _default()
    with open(path) as f:
        p = json.load(f)
    base = _default()
    for k, v in base.items():
        if k not in p:
            p[k] = v
    for role in ROLES:
        p.setdefault(role, {})
        p[role].setdefault("guidance", [])
    return p


def save(p):
    os.makedirs(policy_dir(), exist_ok=True)
    os.makedirs(skills_dir(), exist_ok=True)
    with open(policy_file(), "w") as f:
        json.dump(p, f, indent=2)


# --------------------------------------------------------------------------- #
# Read side — used by the role/prompt code at runtime.                         #
# --------------------------------------------------------------------------- #
def guidance_text(role, p=None):
    p = p or load()
    bullets = p.get(role, {}).get("guidance", [])
    return "\n".join(f"- {b}" for b in bullets)


def skills_for(role, p=None):
    p = p or load()
    out = []
    for s in p.get("skills", []):
        target = s.get("target", "all")
        roles = _TARGET_ALIASES.get(target, (target,))
        if role in roles:
            out.append(s)
    return out


def skill_index_text(role, p=None):
    items = skills_for(role, p)
    return "\n".join(
        f"- {s['id']}: {s.get('name', s['id'])} — use when {s.get('when', '')}" for s in items
    )


def apply(role, base_prompt, p=None):
    """Fold the current policy for `role` into its base system prompt.

    Additive: an empty policy returns base_prompt unchanged, so turning the
    evolver off is a no-op. Read fresh at session-creation time so a newly
    checkpointed policy takes effect on the next run.
    """
    if role not in ROLES:
        return base_prompt
    p = p or load()
    out = base_prompt
    guidance = guidance_text(role, p)
    if guidance:
        out += (
            "\n\nLearned guidance from past runs (follow these unless they conflict "
            "with the task):\n" + guidance
        )
    index = skill_index_text(role, p)
    if index:
        out += (
            "\n\nLearned skills (call read_skill(name) to load one before you start "
            "when it fits):\n" + index
        )
    return out


# --------------------------------------------------------------------------- #
# Write side — used by the evolver to apply updates.                           #
# --------------------------------------------------------------------------- #
def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "skill"


def add_guidance(p, role, bullets):
    """Append new, non-duplicate bullets; cap to MAX_GUIDANCE (drop oldest)."""
    if role not in ROLES:
        return []
    cur = p[role]["guidance"]
    have = {b.strip().lower() for b in cur}
    added = []
    for b in bullets or []:
        b = (b or "").strip()
        if b and b.lower() not in have:
            cur.append(b)
            have.add(b.lower())
            added.append(b)
    if len(cur) > MAX_GUIDANCE:
        p[role]["guidance"] = cur[-MAX_GUIDANCE:]
    return added


def _guidance_match(cur, needle):
    n = (needle or "").strip().lower()
    if not n:
        return []
    return [i for i, b in enumerate(cur) if n in (b or "").strip().lower()]


def remove_guidance(p, role, needles):
    """Drop guidance bullets matching any needle (case-insensitive substring)."""
    if role not in ROLES:
        return []
    cur = p[role]["guidance"]
    drop = set()
    for nd in needles or []:
        drop.update(_guidance_match(cur, nd))
    removed = [cur[i] for i in sorted(drop)]
    if drop:
        p[role]["guidance"] = [b for i, b in enumerate(cur) if i not in drop]
    return removed


def replace_guidance(p, role, pairs):
    """Rewrite bullets in place. Each pair is {old, new}: `old` matches an existing
    bullet by substring (first match) and is overwritten by `new`."""
    if role not in ROLES:
        return []
    cur = p[role]["guidance"]
    applied = []
    for pr in pairs or []:
        if not isinstance(pr, dict):
            continue
        old = (pr.get("old") or "").strip()
        new = (pr.get("new") or "").strip()
        if not old or not new:
            continue
        idxs = _guidance_match(cur, old)
        if not idxs:
            continue
        i = idxs[0]
        others = {b.strip().lower() for j, b in enumerate(cur) if j != i}
        if new.lower() in others:
            continue
        cur[i] = new
        applied.append(new)
    if len(cur) > MAX_GUIDANCE:
        p[role]["guidance"] = cur[-MAX_GUIDANCE:]
    return applied


def add_skill(p, name, target, when, content):
    """Write a skill playbook (skills/<id>/SKILL.md) and register it. Returns id.

    The file matches langbridge_cli.skills' format (YAML frontmatter + body), so
    the existing read_skill tool and catalog pick it up once the policy skills dir
    is on the skills search path.
    """
    target = target if target in ROLES or target in _TARGET_ALIASES else "all"
    base = _slug(name)
    existing = {s["id"] for s in p["skills"]}
    sid, i = base, 2
    while sid in existing:
        sid, i = f"{base}_{i}", i + 1
    skill_path = os.path.join(skills_dir(), sid)
    os.makedirs(skill_path, exist_ok=True)
    desc = (when or name or sid).replace("\n", " ").strip()
    with open(os.path.join(skill_path, "SKILL.md"), "w") as f:
        f.write(f"---\ndescription: {desc}\n---\n\n# {name}\n\n_Use when: {when}_\n\n{content}\n")
    p["skills"].append({"id": sid, "name": name, "when": when, "target": target})
    return sid


def record(p, entry):
    p["version"] = p.get("version", 0) + 1
    entry = dict(entry)
    entry["version"] = p["version"]
    p["history"].append(entry)
    return p["version"]


# --------------------------------------------------------------------------- #
# Checkpointing — snapshot/restore the learned policy during training.         #
# A checkpoint dir is itself a valid POLICY_DIR, so you can pin a frozen        #
# policy for eval/resume with LANGBRIDGE_POLICY_DIR=.../checkpoints/<label>.    #
# --------------------------------------------------------------------------- #
def _copy_policy_into(dest):
    os.makedirs(dest, exist_ok=True)
    pf = policy_file()
    if os.path.exists(pf):
        shutil.copy2(pf, os.path.join(dest, "policy.json"))
    dest_skills = os.path.join(dest, "skills")
    if os.path.isdir(dest_skills):
        shutil.rmtree(dest_skills)
    if os.path.isdir(skills_dir()):
        shutil.copytree(skills_dir(), dest_skills)
    else:
        os.makedirs(dest_skills, exist_ok=True)


def checkpoint(label, metrics=None):
    """Snapshot the live policy under checkpoints/<label>/. Returns the path."""
    dest = os.path.join(checkpoints_dir(), label)
    _copy_policy_into(dest)
    p = load()
    info = {
        "label": label,
        "version": p.get("version"),
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "guidance_counts": {r: len(p.get(r, {}).get("guidance", [])) for r in ROLES},
        "skills": [s["id"] for s in p.get("skills", [])],
        "metrics": metrics or {},
    }
    with open(os.path.join(dest, "meta.json"), "w") as f:
        json.dump(info, f, indent=2)
    return dest


def restore_checkpoint(label):
    src = os.path.join(checkpoints_dir(), label)
    if not os.path.isdir(src):
        raise FileNotFoundError(f"no checkpoint '{label}' under {checkpoints_dir()}")
    os.makedirs(policy_dir(), exist_ok=True)
    src_policy = os.path.join(src, "policy.json")
    if os.path.exists(src_policy):
        shutil.copy2(src_policy, policy_file())
    if os.path.isdir(skills_dir()):
        shutil.rmtree(skills_dir())
    src_skills = os.path.join(src, "skills")
    if os.path.isdir(src_skills):
        shutil.copytree(src_skills, skills_dir())
    else:
        os.makedirs(skills_dir(), exist_ok=True)
    return load()


def list_checkpoints():
    cp_dir = checkpoints_dir()
    if not os.path.isdir(cp_dir):
        return []
    out = []
    for name in os.listdir(cp_dir):
        meta = os.path.join(cp_dir, name, "meta.json")
        if os.path.exists(meta):
            with open(meta) as f:
                out.append(json.load(f))
    return sorted(out, key=lambda m: m.get("saved_at", ""), reverse=True)
