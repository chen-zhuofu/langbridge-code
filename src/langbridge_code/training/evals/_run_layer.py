"""_run_layer.py — run ONE agent layer in this process, against the cwd repo.

The eval adapter spawns this as a subprocess with the working directory
set to a fresh checkout of the target repo at a task's base_commit.

Inputs (env):
  LANGBRIDGE_LAYER     : "workflow" | "coder" | "reviewer" | "l4" | "l3" (legacy aliases)
  LANGBRIDGE_TASK      : the task / problem statement (or read from stdin)
  LANGBRIDGE_CONTEXT   : optional extra context for coder/reviewer
  LANGBRIDGE_MODEL     : model override (else config default)

Output: a single JSON line on stdout:
  {"layer", "report", "approved", "completed", "optimizer_trace"}
"""
import json
import os
import sys


def auto_approve(label, name, arguments):
    return True


def main():
    layer = os.environ.get("LANGBRIDGE_LAYER", "workflow")
    task = os.environ.get("LANGBRIDGE_TASK") or sys.stdin.read()
    task = task.strip()
    context = os.environ.get("LANGBRIDGE_CONTEXT", "")
    if not task:
        print(json.dumps({"error": "no task"}))
        return 1

    from langbridge_code.settings import DEFAULT_MODEL, load_api_key
    from langbridge_code.persistence.session import create_run_log_path
    from langbridge_code.workflow import optimizer_trace

    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    run_log_path = create_run_log_path()

    report = ""
    approved = False
    completed = False

    if layer in ("workflow", "pm"):
        from langbridge_code.workflow.run import run_workflow

        report = run_workflow(
            api_key,
            model,
            task,
            run_log_path,
            turn_id=1,
            print_reply=False,
            approval_callback=auto_approve,
        )
        completed = "Workflow complete" in report
        approved = completed
    elif layer in ("coder", "l4", "l5"):
        from langbridge_code.agents.agent import run_l4_component

        report = run_l4_component(
            api_key,
            model,
            {"task": task, "context": context},
            run_log_path=run_log_path,
            turn_id=1,
            approval_callback=auto_approve,
        )
        approved = "WORKFLOW_REVIEW_STATUS: OK" in report
        completed = approved
    elif layer in ("reviewer", "l3"):
        from langbridge_code.agents.multi_agent import reviewer_review_passed, run_reviewer

        report = run_reviewer(
            api_key,
            model,
            task,
            context,
            run_log_path=run_log_path,
            turn_id=1,
        )
        approved = reviewer_review_passed(report)
        completed = approved
    else:
        print(json.dumps({"error": f"unknown layer {layer}"}))
        return 1

    trace_file = str(optimizer_trace.trace_path(run_log_path))
    print(
        json.dumps(
            {
                "layer": layer,
                "report": report,
                "approved": approved,
                "completed": completed,
                "optimizer_trace": trace_file,
                # Legacy field name for older adapters.
                "shared_worklog": trace_file,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
