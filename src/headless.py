import os
import sys

from langbridge_code import settings
from langbridge_code.agents.main_agent import run_agent_turn
from langbridge_code.settings import load_api_key
from langbridge_code.tools.common.runtime import RuntimeBootstrapError, bootstrap_runtime
from langbridge_code.util.session import create_run_log_path


def auto_approve(label, name, arguments):
    return True


def main():
    try:
        bootstrap_runtime()
    except RuntimeBootstrapError as error:
        print(f"LangBridge runtime setup failed: {error}", file=sys.stderr)
        return 1
    task = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    task = task.strip()
    if not task:
        print("No task provided.", file=sys.stderr)
        return 1

    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL") or settings.DEFAULT_MODEL
    run_log_path = create_run_log_path(task)
    run_agent_turn(
        api_key,
        model,
        task,
        run_log_path,
        turn_id=1,
        approval_callback=auto_approve,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
