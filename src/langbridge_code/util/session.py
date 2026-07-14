import sys

from langbridge_code.settings import MAX_SESSION_CHOICES
from langbridge_code.util.artifacts import (
    create_artifact_session,
    label_artifact_session,
    list_artifact_sessions,
)
from langbridge_code.util.progress import last_progress_turn_id


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


def last_turn_id(run_log_path) -> int:
    """Highest turn id recorded in progress.md for this session."""
    return last_progress_turn_id(run_log_path)
