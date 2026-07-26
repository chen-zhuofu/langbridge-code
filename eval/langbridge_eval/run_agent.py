"""run_agent.py — headless main-agent entry for Docker / bench evals.

Shared by langbridge-bench (and usable by other benches). Run inside a task
checkout:

  LANGBRIDGE_TASK='...' python -m langbridge_eval.run_agent
"""
import json
import os
import sys

_TASK_WRAPPER = """\
You are working in a checked-out code repository (current working directory).
Implement the minimal code changes needed to resolve the issue below.
Do not only explain or answer questions — edit source files to fix the problem.
Do not modify existing tests unless the issue explicitly requires it.
Do not browse live GitHub/Jira for the answer; use this repo and the text below.

<issue>
{issue}
</issue>
"""


def auto_approve(label, name, arguments):
    return True


def wrap_issue_as_task(issue: str) -> str:
    """Wrap a raw issue/problem statement as an implement-the-fix task."""
    issue = (issue or "").strip()
    if not issue:
        return ""
    return _TASK_WRAPPER.format(issue=issue).strip()


def main():
    """Subprocess entry: run main agent against cwd (the target repo checkout)."""
    issue = os.environ.get("LANGBRIDGE_TASK") or sys.stdin.read()
    issue = issue.strip()
    if not issue:
        print(json.dumps({"error": "no task"}))
        return 1

    task = wrap_issue_as_task(issue)

    from langbridge_code import settings
    from langbridge_code.settings import load_api_key
    from langbridge_code.tools.common.runtime import RuntimeBootstrapError, bootstrap_runtime
    from langbridge_code.util.session import create_run_log_path
    from langbridge_code.util import optimizer_trace
    from langbridge_code.agents.main_agent import run_agent_turn
    from langbridge_eval.telemetry import start_telemetry

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
