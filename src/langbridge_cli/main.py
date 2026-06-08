import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from langbridge_cli.tools import TOOL_SCHEMAS, TOOLS


API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.1-codex"
CONFIG_DIR = Path.home() / ".langbridge"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history"
MAX_AGENT_STEPS = 20
WORKSPACE_ROOT = Path.cwd().resolve()
RUNS_DIR = WORKSPACE_ROOT / "session-history"


def main():
    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    run_log_path = create_run_log_path()
    session = create_prompt_session() if sys.stdin.isatty() else None

    messages = [
        {
            "role": "system",
            "content": "You are langbridge-cli, a concise coding agent. Help the user implement software step by step.",
        }
    ]

    print(f"langbridge-cli using {model}")
    print(f"Agent loop log: {run_log_path}")
    print("Type /exit to quit.\n")

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

        messages.append({"role": "user", "content": text})
        reply = run_agent(api_key, model, messages, run_log_path)
        messages.append({"role": "assistant", "content": reply})
        print(f"\n{reply}\n")


def load_api_key():
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return api_key

    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())["api_key"]

    api_key = getpass.getpass("Enter Codex API key: ")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"api_key": api_key}, indent=2))
    CONFIG_PATH.chmod(0o600)
    return api_key


def create_run_log_path():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return RUNS_DIR / f"{timestamp}.json"


def create_prompt_session():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return PromptSession(history=FileHistory(str(HISTORY_PATH)))


def read_user_input(session):
    if session is not None:
        return session.prompt("langbridge> ")
    return input("langbridge> ")


def run_agent(api_key, model, messages, run_log_path):
    agent_input = list(messages)

    for step in range(MAX_AGENT_STEPS):
        write_agent_input_log(run_log_path, step, agent_input)
        data = create_response(api_key, model, agent_input)
        output = data.get("output", [])
        tool_calls = [item for item in output if item.get("type") == "function_call"]

        if not tool_calls:
            return extract_output_text(output)

        agent_input.extend(output)
        for call in tool_calls:
            tool_output = run_tool_call(call)
            agent_input.append(tool_output)

    return "Agent stopped because it reached the maximum tool-call steps."


def create_response(api_key, model, agent_input):
    body = json.dumps({"model": model, "input": agent_input, "tools": TOOL_SCHEMAS}).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        data = json.loads(error.read())
        raise RuntimeError(data.get("error", {}).get("message", "OpenAI request failed"))

    return data


def run_tool_call(call):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = json.loads(call.get("arguments") or "{}")
        if name not in TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        output = TOOLS[name](**arguments)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def write_agent_input_log(run_log_path, step, agent_input):
    record = {"step": step, "agent_input": agent_input}
    records = []
    if run_log_path.exists():
        records = json.loads(run_log_path.read_text(encoding="utf-8"))

    records.append(record)
    run_log_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def extract_output_text(output):
    return "".join(
        content["text"]
        for item in output
        for content in item.get("content", [])
        if content["type"] == "output_text"
    )


if __name__ == "__main__":
    main()
