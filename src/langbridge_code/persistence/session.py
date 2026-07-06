import json
import sys
from datetime import datetime

from langbridge_cli.llm.client import create_model_response
from langbridge_cli.settings import (
    MAX_SESSION_CHOICES,
    MAX_SESSION_SUMMARY_INPUT_CHARS,
    RUNS_DIR,
)
from langbridge_cli.llm.parse import extract_output_text, truncate_text


def create_run_log_path():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return RUNS_DIR / f"{timestamp}.json"


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
    if not RUNS_DIR.exists():
        return []
    return sorted(RUNS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)


def label_session(path):
    try:
        session_log = read_session_log(path)
    except (OSError, json.JSONDecodeError):
        return f"{path.stem} - unreadable session"

    return f"{path.stem} - {session_log['summary']}"


def read_session_log(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"summary": "", "turns": data}
    return {"summary": data.get("summary", ""), "turns": data.get("turns", [])}


def read_session_records(path):
    return read_session_log(path)["turns"]


def write_session_summary(api_key, model, run_log_path):
    session_log = read_session_log(run_log_path)
    if session_log["summary"]:
        return

    session_log["summary"] = create_session_summary(api_key, model, session_log["turns"])
    run_log_path.write_text(
        json.dumps(session_log, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_session_summary(api_key, model, records):
    prompt = (
        "Summarize this coding-agent CLI session as a short title for a session picker. "
        "Return only the title, no punctuation wrapper, under 12 words.\n\n"
        f"{session_summary_input(records)}"
    )
    data = create_text_response(
        api_key,
        model,
        [
            {"role": "system", "content": "You write concise session titles."},
            {"role": "user", "content": prompt},
        ],
    )
    return extract_output_text(data.get("output", [])).strip()


def create_text_response(api_key, model, agent_input):
    return create_model_response(api_key, model, agent_input, label="session summary")


def session_summary_input(records):
    lines = []
    for record in records[-5:]:
        user = record.get("user")
        assistant = record.get("assistant")
        if user:
            lines.append(f"User: {truncate_text(user, 300)}")
        if assistant:
            lines.append(f"Assistant: {truncate_text(assistant, 300)}")

    return truncate_text("\n".join(lines), MAX_SESSION_SUMMARY_INPUT_CHARS)


def last_turn_id(records):
    turn_ids = [record.get("turn_id", 0) for record in records]
    return max(turn_ids, default=0)
