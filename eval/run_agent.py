"""run_agent.py — headless main-agent entry for Docker / bench evals.

Shared by langbridge-bench (and usable by other benches). Run inside a task
checkout:

  LANGBRIDGE_TASK='...' python -m run_agent
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

_TASK_PROMPT = Path(__file__).resolve().parent / "prompt" / "task.py"


def _load_task_wrapper() -> str:
    """Load ``eval/prompt/task.py`` (sibling of this module)."""
    if not _TASK_PROMPT.is_file():
        raise FileNotFoundError(
            f"eval task prompt missing: {_TASK_PROMPT} "
            "(docker runner must copy eval/prompt into the container)"
        )
    spec = importlib.util.spec_from_file_location("eval_prompt_task", _TASK_PROMPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load eval task prompt: {_TASK_PROMPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TASK_WRAPPER


def auto_approve(label, name, arguments):
    return True


def wrap_issue_as_task(issue: str) -> str:
    """Wrap a raw issue/problem statement as an implement-the-fix task."""
    issue = (issue or "").strip()
    if not issue:
        return ""
    return _load_task_wrapper().format(issue=issue).strip()


def main():
    """Subprocess entry: run main agent against cwd (the target repo checkout)."""
    issue = os.environ.get("LANGBRIDGE_TASK") or sys.stdin.read()
    issue = issue.strip()
    if not issue:
        print(json.dumps({"error": "no task"}))
        return 1

    try:
        task = wrap_issue_as_task(issue)
    except (FileNotFoundError, ImportError) as err:
        print(json.dumps({"error": str(err)}))
        return 1

    from langbridge_code import settings
    from langbridge_code.settings import load_api_key
    from langbridge_code.tools.common.runtime import RuntimeBootstrapError, bootstrap_runtime
    from langbridge_code.util.session import create_run_log_path
    from langbridge_code.util import optimizer_trace
    from langbridge_code.agents.main_agent import run_agent_turn
    from util.telemetry import start_telemetry

    try:
        bootstrap_runtime()
    except RuntimeBootstrapError as error:
        print(json.dumps({"error": f"runtime bootstrap failed: {error}"}))
        return 1

    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL") or settings.DEFAULT_MODEL
    # Name the session from the raw issue so titles stay short/readable.
    run_log_path = create_run_log_path(issue)

    with start_telemetry() as tel:
        report = run_agent_turn(
            api_key,
            model,
            task,
            run_log_path,
            turn_id=1,
            print_reply=False,
            approval_callback=auto_approve,
        )
        telemetry = tel.snapshot()

    trace_file = str(optimizer_trace.trace_path(run_log_path))
    print(
        json.dumps(
            {
                "report": report,
                "optimizer_trace": trace_file,
                "shared_worklog": trace_file,
                "telemetry": telemetry,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
