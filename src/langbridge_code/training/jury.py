"""Offline dispute jury for the evolver when hidden tests are unavailable.

Runtime L4/L5 loops are a simple coder/reviewer back-and-forth (worktrial style).
During training, grade() may return no ground truth — then jury_fn acts as the
correctness anchor via two independent L3 reviewers.
"""
from concurrent.futures import ThreadPoolExecutor

from langbridge_cli.agents.multi_agent import l3_review_passed, run_l3_test_engineer

JUROR_COUNT = 2


def juror_context(context, worker_report, worker_label="L4"):
    parts = []
    if context:
        parts.append(context)
    parts.append(
        f"You are an independent juror. Verify the {worker_label} implementation "
        "on its own merits and vote PASS or FAIL."
    )
    parts.append(f"{worker_label} implementation to verify:\n{worker_report}")
    return "\n\n".join(parts)


def _run_jurors(api_key, model, task, prompt):
    with ThreadPoolExecutor(max_workers=JUROR_COUNT) as pool:
        futures = [
            pool.submit(run_l3_test_engineer, api_key, model, task, prompt)
            for _ in range(JUROR_COUNT)
        ]
        return [future.result() for future in futures]


def make_jury_fn(api_key, model):
    """Return jury_fn(spec, trace) -> {jury_pass, verified} for the evolver."""

    def jury_fn(spec, trace):
        task = spec.get("problem_statement") or trace.get("task") or ""
        context = spec.get("context", "")
        worker = (trace.get("worker") or "l4").lower()
        worker_label = "L5" if worker == "l5" else "L4"
        report = trace.get("final_report") or trace.get("final_diff") or ""
        if not report.strip():
            rounds = trace.get("rounds") or []
            if rounds:
                report = rounds[-1].get("worker_report") or report
        if not report.strip():
            return {"jury_pass": None, "verified": False}

        prompt = juror_context(context, report, worker_label)
        reports = _run_jurors(api_key, model, task, prompt)
        jury_pass = all(l3_review_passed(report) for report in reports)
        return {"jury_pass": jury_pass, "verified": True}

    return jury_fn
