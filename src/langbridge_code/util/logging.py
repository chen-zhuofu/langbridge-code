import json

from langbridge_code.llm.parse import extract_turn_user_input
from langbridge_code.util.session import read_session_log


def write_input_log(run_log_path, turn_id, messages):
    record_user_turn(run_log_path, turn_id, extract_turn_user_input(messages))


def record_user_turn(run_log_path, turn_id, user_text: str):
    """Persist the user's message as soon as they submit (before the agent runs)."""
    if run_log_path is None:
        return
    upsert_turn_record(
        run_log_path,
        {
            "turn_id": turn_id,
            "user": (user_text or "").strip(),
            "assistant": "",
        },
    )


def write_finish_log(run_log_path, turn_id, finished):
    record = read_turn_record(run_log_path, turn_id)
    if record is None:
        record = {"turn_id": turn_id, "user": "", "assistant": ""}
    record["assistant"] = finished
    upsert_turn_record(run_log_path, record)


def write_turn_complete(run_log_path, turn_id, user_text: str, assistant_text: str):
    """Persist user + assistant together when a main-agent turn finishes."""
    if run_log_path is None:
        return
    upsert_turn_record(
        run_log_path,
        {
            "turn_id": turn_id,
            "user": (user_text or "").strip(),
            "assistant": (assistant_text or "").strip(),
        },
    )


def read_turn_record(run_log_path, turn_id):
    if not run_log_path.exists():
        return None
    session_log = read_session_log(run_log_path)
    for record in session_log["turns"]:
        if record.get("turn_id") == turn_id:
            return record
    return None


def upsert_turn_record(run_log_path, record):
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    session_log = {"summary": "", "turns": []}
    if run_log_path.exists():
        session_log = read_session_log(run_log_path)

    slim = {
        "turn_id": record.get("turn_id"),
        "user": record.get("user", ""),
        "assistant": record.get("assistant", ""),
    }
    for index, existing_record in enumerate(session_log["turns"]):
        if existing_record.get("turn_id") == slim["turn_id"]:
            if not slim["user"]:
                slim["user"] = existing_record.get("user", "")
            if not slim["assistant"]:
                slim["assistant"] = existing_record.get("assistant", "")
            session_log["turns"][index] = slim
            break
    else:
        session_log["turns"].append(slim)

    run_log_path.write_text(
        json.dumps(session_log, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
