"""Legacy agent entry points — workflow replaces PM/L4/L5 loops."""
from langbridge_code.workflow.coder_reviewer import run_coder_reviewer_loop
from langbridge_code.workflow.run import run_pm_loop, run_workflow


def pm_should_continue(_finished):
    """Workflow runs to completion in one call; no BUG_STATUS continuation."""
    return False


def strip_bug_status(finished):
    return finished.rstrip()


def run_l4_component(api_key, model, arguments, trace_sink=None, run_log_path=None, turn_id=None, approval_callback=None):
    task = arguments.get("task", "")
    context = arguments.get("context", "")
    passed, detail = run_coder_reviewer_loop(
        api_key,
        model,
        task,
        context,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
        approval_callback=approval_callback,
    )
    status = "OK" if passed else "NEEDS_WORK"
    return f"{detail}\n\nWORKFLOW_REVIEW_STATUS: {status}"


def run_l5_component(api_key, model, arguments, trace_sink=None, run_log_path=None, turn_id=None, approval_callback=None):
    return (
        "L5 path removed. Use workflow planner for hard tasks.\n\n"
        "WORKFLOW_REVIEW_STATUS: NEEDS_WORK"
    )


def run_tool_call(*args, **kwargs):
    raise NotImplementedError("PM tool loop removed; use run_workflow.")
