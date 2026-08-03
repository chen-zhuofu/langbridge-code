"""Load prompt bodies from sibling ``.md`` files under ``src/prompt/``."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(relative_path: str) -> str:
    """Return the text of ``src/prompt/<relative_path>`` (utf-8, trailing newline kept)."""
    path = (_PROMPT_ROOT / relative_path).resolve()
    if not path.is_relative_to(_PROMPT_ROOT):
        raise ValueError(f"prompt path escapes prompt root: {relative_path}")
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(relative_path: str, **fields) -> str:
    """Load a prompt template and format ``{placeholders}``."""
    return load_prompt(relative_path).format(**fields)
