"""Shared worker<->L3 worklog: the durable record of the review negotiation.

Each turn of the review loop appends an entry that ends with a status token. The
token is what the loop routes on, so this shared ledger is the negotiation's
decision record, not a side note. It lives under the worker that drives the
review: L4<->L3 negotiations in l34_share_worklog.md under L4_WORKLOG_DIR, L5<->L3
in l45_share_worklog.md under L5_WORKLOG_DIR. When there is no active run
(run_log_path is None, e.g. in unit tests) the writer is a no-op so it never
litters the workspace.
"""

from langbridge_cli import config


def _worklog_dir(worker_label):
    if worker_label == "L5":
        return config.L5_WORKLOG_DIR
    return config.L4_WORKLOG_DIR


def _worklog_filename(worker_label):
    return "l45_share_worklog.md" if worker_label == "L5" else "l34_share_worklog.md"


def worklog_path(run_log_path, worker_label="L4"):
    if run_log_path is None:
        return None
    return _worklog_dir(worker_label) / _worklog_filename(worker_label)


def start_worklog(run_log_path, task, worker_label="L4"):
    path = worklog_path(run_log_path, worker_label)
    if path is None:
        return
    _append(path, [f"## {worker_label}<->L3 negotiation: {task}", ""])


def append_worklog_entry(run_log_path, role, text, token, worker_label="L4"):
    path = worklog_path(run_log_path, worker_label)
    if path is None:
        return
    _append(path, [f"### {role}", "", text.strip(), "", f"WORKLOG_TOKEN: {token}", ""])


def _append(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
