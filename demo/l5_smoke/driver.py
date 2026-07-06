import os
import sys
from pathlib import Path

# Make langbridge_code importable from the repo checkout regardless of install state.
REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from langbridge_code.agent import run_l5_component
from langbridge_code.settings import DEFAULT_MODEL, load_api_key
from langbridge_code.session import create_run_log_path


def auto_approve(label, name, arguments):
    return True


TASK = (
    "Build a small Python library in the current directory: a file bank.py with an "
    "Account class supporting deposit(amount), withdraw(amount) that raises "
    "ValueError on overdraft, and a balance property; plus a transfer(src, dst, "
    "amount) function that moves money between two accounts atomically. Add focused "
    "unit tests, and an integration test that exercises a realistic multi-step "
    "transfer scenario end to end."
)


def main():
    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    run_log = create_run_log_path()
    print(f"=== L5 smoke test ===\ncwd={os.getcwd()}\nmodel={model}\nrun_log={run_log}\n")
    output = run_l5_component(
        api_key,
        model,
        {"task": TASK, "context": "Fresh empty directory; create everything from scratch."},
        run_log_path=run_log,
        turn_id=1,
        approval_callback=auto_approve,
    )
    print("\n=== L5 component delivery ===")
    print(output)


if __name__ == "__main__":
    main()
