"""Load prompt modules from ``data-pipeline/prompt/<name>.py`` by path.

Avoids a top-level ``prompt`` package name that would collide with
``eval/prompt`` when both trees are on ``sys.path``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompt"


def load_prompt(name: str) -> ModuleType:
    """Import ``prompt/<name>.py`` as ``data_pipeline_prompt_<name>``."""
    path = _PROMPT_DIR / f"{name}.py"
    mod_name = f"data_pipeline_prompt_{name}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load prompt module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
