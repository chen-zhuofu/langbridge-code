import os
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from langbridge_cli.agents.agent import run_pm_loop
from langbridge_cli.config import (
    CONFIG_DIR,
    DEFAULT_MODEL,
    HISTORY_PATH,
    load_api_key,
)
from langbridge_cli.persistence.session import (
    create_run_log_path,
    last_turn_id,
    read_session_records,
    select_previous_session,
    write_session_summary,
)


def main():
    # The Textual UI is the default; set LANGBRIDGE_TERMINAL=1 for the plain REPL.
    if os.environ.get("LANGBRIDGE_TERMINAL", "").strip().lower() not in {"1", "true", "yes", "on"}:
        from langbridge_cli.ui.tui import run_tui

        run_tui()
        return

    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    session = create_prompt_session() if sys.stdin.isatty() else None

    previous_session = select_previous_session(session)
    if previous_session is not None:
        records = read_session_records(previous_session)
        run_log_path = previous_session
        turn_id = last_turn_id(records)
    else:
        run_log_path = create_run_log_path()
        turn_id = 0

    print(f"langbridge-cli using {model}")
    print(f"Agent loop log: {run_log_path}")

    while True:
        try:
            text = read_user_input(session)
        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            break

        if text.strip() == "/exit":
            break

        turn_id += 1
        run_pm_loop(api_key, model, text, run_log_path, turn_id)
        write_session_summary(api_key, model, run_log_path)


def create_prompt_session():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return PromptSession(history=FileHistory(str(HISTORY_PATH)))


def read_user_input(session):
    if session is not None:
        return session.prompt("langbridge> ")
    return input("langbridge> ")


if __name__ == "__main__":
    main()
