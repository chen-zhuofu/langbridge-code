"""_run_layer.py — run ONE agent layer in this process, against the cwd repo.

The eval/evolver adapter spawns this as a subprocess with the working directory
set to a fresh checkout of the target repo at a task's base_commit. Because the
filesystem/exec/test tools pin WORKSPACE_ROOT to cwd at import time, running each
agent in its own process is how we sandbox it to the right repo.

Inputs (env):
  LANGBRIDGE_LAYER     : "pm" | "l4" | "l5" | "l3"
  LANGBRIDGE_TASK      : the task / problem statement (or read from stdin)
  LANGBRIDGE_CONTEXT   : optional extra context for l4/l5/l3
  LANGBRIDGE_MODEL     : model override (else config default)
  plus the usual LANGBRIDGE_AGENT_STATE_DIR / _RUNS_DIR / _POLICY_DIR redirection.

Output: a single JSON line on stdout:
  {"layer", "report", "approved", "completed", "shared_worklog"}
The candidate's code changes are read by the parent via `git diff`, not here.
"""
import json
import os
import sys


def auto_approve(label, name, arguments):
    return True


def _shared_worklog_path(run_log_path):
    """Best-effort: find the L4/L5<->L3 shared worklog this run produced."""
    try:
        from langbridge_cli import config

        for d in (config.L4_WORKLOG_DIR, config.L5_WORKLOG_DIR):
            if not os.path.isdir(d):
                continue
            hits = []
            for root, _dirs, files in os.walk(d):
                for f in files:
                    if "share" in f and f.endswith(".md"):
                        full = os.path.join(root, f)
                        hits.append((os.path.getmtime(full), full))
            if hits:
                return max(hits)[1]
    except Exception:
        pass
    return ""


def main():
    layer = os.environ.get("LANGBRIDGE_LAYER", "pm")
    task = os.environ.get("LANGBRIDGE_TASK") or sys.stdin.read()
    task = task.strip()
    context = os.environ.get("LANGBRIDGE_CONTEXT", "")
    if not task:
        print(json.dumps({"error": "no task"}))
        return 1

    from langbridge_cli.config import DEFAULT_MODEL, load_api_key
    from langbridge_cli.persistence.session import create_run_log_path

    api_key = load_api_key()
    model = os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    run_log_path = create_run_log_path()

    report = ""
    approved = False
    completed = False

    if layer == "pm":
        from langbridge_cli.agents.agent import pm_should_continue, run_pm_loop

        report = run_pm_loop(api_key, model, task, run_log_path, turn_id=1,
                             print_reply=False, approval_callback=auto_approve)
        completed = not pm_should_continue(report)
        approved = completed
    elif layer in ("l4", "l5"):
        from langbridge_cli.agents.agent import run_l4_component, run_l5_component

        fn = run_l4_component if layer == "l4" else run_l5_component
        report = fn(api_key, model, {"task": task, "context": context},
                    run_log_path=run_log_path, turn_id=1, approval_callback=auto_approve)
        approved = "PM_REVIEW_STATUS: OK" in report
    elif layer == "l3":
        from langbridge_cli.agents.multi_agent import l3_review_passed, run_l3_test_engineer

        report = run_l3_test_engineer(api_key, model, task, context,
                                      run_log_path=run_log_path, turn_id=1)
        approved = l3_review_passed(report)
    else:
        print(json.dumps({"error": f"unknown layer {layer}"}))
        return 1

    print(json.dumps({
        "layer": layer,
        "report": report,
        "approved": approved,
        "completed": completed,
        "shared_worklog": _shared_worklog_path(run_log_path),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
