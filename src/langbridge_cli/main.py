import os
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from langbridge_cli.agent import run_agent
from langbridge_cli.config import (
    CONFIG_DIR,
    DEFAULT_MODEL,
    HISTORY_PATH,
    load_api_key,
)
from langbridge_cli.logging import write_session_summary
from langbridge_cli.prompt import SYSTEM_PROMPT
from langbridge_cli.session import (
    create_run_log_path,
    last_turn_id,
    read_session_records,
    restore_session_messages,
    select_previous_session,
)


def main():
    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    session = create_prompt_session() if sys.stdin.isatty() else None

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]
    previous_session = select_previous_session(session)
    if previous_session is not None:
        records = read_session_records(previous_session)
        messages = restore_session_messages(records) or messages
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
        messages.append({"role": "user", "content": text})
        run_agent(api_key, model, messages, run_log_path, turn_id)
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
