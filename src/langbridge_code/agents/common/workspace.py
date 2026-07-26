"""Thread-local workspace root for parallel worktree workers (not an LLM tool)."""
import threading
from contextlib import contextmanager
from pathlib import Path

_tls = threading.local()

# Directories outside the workspace that read tools may access (read-only).
# Session artifact dirs register here so agents can read persisted reports.
_readable_roots: set[Path] = set()
_readable_roots_lock = threading.Lock()


def add_readable_root(path) -> None:
    if path is None:
        return
    with _readable_roots_lock:
        _readable_roots.add(Path(path).resolve())


def readable_roots() -> tuple[Path, ...]:
    with _readable_roots_lock:
        return tuple(_readable_roots)


def get_workspace_root() -> Path:
    if hasattr(_tls, "root"):
        return _tls.root
    from langbridge_code.settings import WORKSPACE_ROOT

    return Path(WORKSPACE_ROOT).resolve()


def get_plan_file_override() -> Path | None:
    return getattr(_tls, "plan_file", None)


def set_workspace_root(path: Path | None) -> None:
    if path is None:
        if hasattr(_tls, "root"):
            delattr(_tls, "root")
    else:
        _tls.root = path.resolve()


@contextmanager
def plan_file_scope(path: Path | None):
    """Temporarily map relative todo_list.md access to a session artifact."""
    previous = getattr(_tls, "plan_file", None)
    if path is None:
        if hasattr(_tls, "plan_file"):
            delattr(_tls, "plan_file")
    else:
        _tls.plan_file = Path(path).resolve()
    try:
        yield
    finally:
        if previous is None:
            if hasattr(_tls, "plan_file"):
                delattr(_tls, "plan_file")
        else:
            _tls.plan_file = previous


@contextmanager
def workspace_scope(path: Path):
    previous = getattr(_tls, "root", None)
    set_workspace_root(path)
    try:
        yield path.resolve()
    finally:
        if previous is None:
            set_workspace_root(None)
        else:
            set_workspace_root(previous)
