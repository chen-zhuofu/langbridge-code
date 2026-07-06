"""Backward-compatible alias for langbridge_bench (old module name)."""
from langbridge_cli.training.langbridge_bench import (  # noqa: F401
    INSTANCES_DIR,
    SPECS_DIR,
    Workspaces,
    make_callables,
    make_grader,
    specs,
)

__all__ = ["INSTANCES_DIR", "SPECS_DIR", "Workspaces", "make_callables", "make_grader", "specs"]
