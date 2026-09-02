"""Per-user paths that survive LangBridge app and repository updates."""
from __future__ import annotations

import os
from pathlib import Path


def app_support_dir() -> Path:
    override = os.environ.get("LANGBRIDGE_APP_SUPPORT_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "LangBridge"
    ).resolve()


def app_skills_dir(role: str = "langbridge") -> Path:
    return app_support_dir() / "skills" / role
