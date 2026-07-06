import os
import sys

from langbridge_cli.workflow.run import run_workflow
from langbridge_cli.settings import DEFAULT_MODEL, load_api_key
from langbridge_cli.persistence.session import create_run_log_path


def auto_approve(label, name, arguments):
    return True


def main():
    task = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    task = task.strip()
    if not task:
        print("No task provided.", file=sys.stderr)
        return 1

    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    run_log_path = create_run_log_path()
    run_workflow(
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
