"""Thread-local workspace root and per-agent artifact read ACL."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

_tls = threading.local()


def _state():
    if not hasattr(_tls, "readable_roots"):
        _tls.readable_roots = set()
        _tls.session_dir = None
        _tls.agent_label = None
        _tls.task_name = ""
    return _tls


def add_readable_root(path) -> None:
    """Allow read_file to follow absolute paths under ``path`` (dir or file)."""
    if path is None:
        return
    _state().readable_roots.add(Path(path).resolve())


def clear_readable_roots() -> None:
    _state().readable_roots.clear()


def readable_roots() -> tuple[Path, ...]:
    return tuple(_state().readable_roots)


def get_agent_session_dir() -> Path | None:
    session = _state().session_dir
    return Path(session).resolve() if session is not None else None


def get_agent_label() -> str | None:
    return _state().agent_label


def get_agent_task_name() -> str:
    return _state().task_name or ""


def get_agent_artifact_state() -> dict:
    st = _state()
    return {
        "readable_roots": set(st.readable_roots),
        "session_dir": st.session_dir,
        "agent_label": st.agent_label,
        "task_name": st.task_name,
    }


def set_agent_artifact_state(state: dict | None) -> None:
    st = _state()
    if state is None:
        st.readable_roots = set()
        st.session_dir = None
        st.agent_label = None
        st.task_name = ""
        return
    st.readable_roots = set(state.get("readable_roots") or ())
    st.session_dir = state.get("session_dir")
    st.agent_label = state.get("agent_label")
    st.task_name = state.get("task_name") or ""


def configure_agent_artifacts(run_log_path, *, label: str, task_name: str = "") -> None:
    """Bind per-agent readable roots for the current thread.

    Main may read its own attachments, session progress/todo_list, task
    progress files, and explorer reports. Subagents may only read their
    role directory under the task (progress + attachments). Engine-only
    paths (traces.md, session.md, log attachments, task traces/) are
    always denied.
    """
    from langbridge_code.agents.common.todo_list import PLAN_FILENAME
    from langbridge_code.util.artifacts import (
        LEGACY_PROGRESS_MD,
        MAIN_DIRNAME,
        SESSION_MEMORY_MD,
        artifact_dir,
        role_dir_name,
        task_role_dir,
    )

    st = _state()
    st.readable_roots = set()
    st.agent_label = label
    st.task_name = (task_name or "").strip()
    directory = artifact_dir(run_log_path)
    if directory is None:
        st.session_dir = None
        return
    session = directory.resolve()
    st.session_dir = session
    role = role_dir_name(label)
    if role == MAIN_DIRNAME:
        st.readable_roots.add(session / MAIN_DIRNAME)
        st.readable_roots.add(session / SESSION_MEMORY_MD)
        st.readable_roots.add(session / LEGACY_PROGRESS_MD)
        st.readable_roots.add(session / PLAN_FILENAME)
        return
    role_path = task_role_dir(session, st.task_name, role)
    if role_path is not None:
        st.readable_roots.add(role_path.resolve())


def _main_may_read_task_path(resolved: Path, session: Path) -> bool:
    """Main can read task session_memory.md files and explorer reports/ only."""
    from langbridge_code.util.artifacts import (
        LEGACY_PROGRESS_MD,
        SESSION_MEMORY_MD,
        TASKS_DIRNAME,
        is_engine_only_path,
    )

    if is_engine_only_path(resolved, session):
        return False
    tasks = session / TASKS_DIRNAME
    try:
        rel = resolved.relative_to(tasks)
    except ValueError:
        return False
    if not rel.parts:
        return False
    if resolved.name in {SESSION_MEMORY_MD, LEGACY_PROGRESS_MD}:
        return True
    return "reports" in rel.parts


def can_read_artifact(path) -> bool:
    """Whether the current agent may read_file ``path`` outside the workspace."""
    from langbridge_code.util.artifacts import MAIN_DIRNAME, is_engine_only_path, role_dir_name

    resolved = Path(path).resolve()
    st = _state()
    session = st.session_dir
    if session is not None and is_engine_only_path(resolved, session):
        return False
    for root in st.readable_roots:
        root_path = Path(root)
        if root_path.is_file() or not root_path.exists():
            # File roots (session_memory.md / todo_list.md) match exactly.
            if resolved == root_path.resolve():
                return True
            # Non-existent dir roots still authorize children once created.
            if not root_path.exists() and not root_path.suffix:
                try:
                    resolved.relative_to(root_path.resolve())
                    return True
                except ValueError:
                    pass
            continue
        if root_path.is_dir() and resolved.is_relative_to(root_path):
            return True
    if session is not None and role_dir_name(st.agent_label or "") == MAIN_DIRNAME:
        return _main_may_read_task_path(resolved, Path(session).resolve())
    return False


@contextmanager
def agent_artifact_scope(state: dict | None):
    """Re-bind artifact ACL in a worker thread (like workspace_scope)."""
    previous = get_agent_artifact_state()
    set_agent_artifact_state(state)
    try:
        yield
    finally:
        set_agent_artifact_state(previous)


@contextmanager
def nested_agent_artifacts(run_log_path, *, label: str, task_name: str = ""):
    """Configure a subagent ACL and restore the caller's ACL on exit."""
    previous = get_agent_artifact_state()
    configure_agent_artifacts(run_log_path, label=label, task_name=task_name)
    try:
        yield
    finally:
        set_agent_artifact_state(previous)


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
