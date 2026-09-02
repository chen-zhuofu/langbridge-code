import shutil
import sys
from pathlib import Path

from langbridge_code.settings import MAX_SESSION_CHOICES
from langbridge_code.util.artifacts import (
    create_artifact_session,
    format_session_timestamp,
    label_artifact_session,
    list_artifact_sessions,
    rename_artifact_session,
)

FORK_MEMORY_CONTEXT_FILE = ".fork-memory-context.md"


def create_run_log_path(first_user_message: str | None = None):
    """Create artifact session directory. Requires first user message for naming."""
    if not first_user_message or not first_user_message.strip():
        raise ValueError("first_user_message is required to create an artifact session")
    return create_artifact_session(first_user_message.strip())


def ensure_run_log_path(run_log_path, first_user_message: str):
    if run_log_path is not None:
        return run_log_path
    return create_run_log_path(first_user_message)


def select_previous_session(session):
    if not sys.stdin.isatty():
        return None

    logs = list_session_logs()
    if not logs:
        return None

    print("\nChoose a session.")
    print("0. Start new session")
    for index, path in enumerate(logs[:MAX_SESSION_CHOICES], start=1):
        print(f"{index}. {label_session(path)}")

    answer = read_selection_input(session)
    if not answer or answer == "0":
        return None

    try:
        selected_index = int(answer)
    except ValueError:
        print("Invalid selection; starting fresh.")
        return None

    if not 1 <= selected_index <= min(len(logs), MAX_SESSION_CHOICES):
        print("Invalid selection; starting fresh.")
        return None
    return logs[selected_index - 1]


def read_selection_input(session):
    prompt = "Select session: "
    if session is not None:
        return session.prompt(prompt).strip()
    return input(prompt).strip()


def list_session_logs():
    return list_artifact_sessions()


def label_session(path):
    return label_artifact_session(path)


def rename_session(path, title):
    return rename_artifact_session(path, title)


def last_turn_id(run_log_path) -> int:
    """Highest turn id recorded in traces.md for this session."""
    from langbridge_code.util.session_traces import last_traces_turn_id

    return last_traces_turn_id(run_log_path)


def read_fork_memory_context(session_path) -> tuple[bool, str]:
    """Return the one-shot memory snapshot attached to a session fork."""
    if session_path is None:
        return False, ""
    path = Path(session_path) / FORK_MEMORY_CONTEXT_FILE
    if not path.is_file():
        return False, ""
    return True, path.read_text(encoding="utf-8")


def clear_fork_memory_context(session_path) -> None:
    """Consume a fork memory snapshot after the new agent context is ready."""
    if session_path is None:
        return
    (Path(session_path) / FORK_MEMORY_CONTEXT_FILE).unlink(missing_ok=True)


def fork_session(source_path, *, memory_context: str | None = None) -> Path:
    """Copy a session's durable artifacts into a new, independent session.

    Duplicates the source directory wholesale (session memory, traces, and
    task artifacts) so the fork has the same context snapshot for its next
    agent turn. ``memory_context`` carries the source agent's already-selected
    memory block so the fork does not prefetch again. The source is untouched.
    """
    source = Path(source_path)
    if not source.is_dir():
        raise FileNotFoundError(source)
    label = label_artifact_session(source)
    fork_dir = source.parent / f"{source.name}-fork-{format_session_timestamp()}"
    suffix = 1
    while fork_dir.exists():
        fork_dir = source.parent / f"{source.name}-fork-{format_session_timestamp()}-{suffix}"
        suffix += 1
    shutil.copytree(source, fork_dir)
    rename_artifact_session(fork_dir, f"{label} (fork)")
    (fork_dir / FORK_MEMORY_CONTEXT_FILE).write_text(
        str(memory_context or ""), encoding="utf-8"
    )
    return fork_dir
