import threading

from langbridge_code.training import jury as jury_module


def test_run_jurors_execute_concurrently(monkeypatch):
    active = {"count": 0, "max": 0}
    lock = threading.Lock()
    both_started = threading.Event()

    def fake_l3(*args, **kwargs):
        with lock:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
            if active["count"] == jury_module.JUROR_COUNT:
                both_started.set()
        assert both_started.wait(timeout=2)
        with lock:
            active["count"] -= 1
        return "REVIEW_VERDICT: PASS\nEvidence: ok"

    monkeypatch.setattr(jury_module, "run_l3_test_engineer", fake_l3)

    reports = jury_module._run_jurors("key", "model", "task", "context")

    assert len(reports) == 2
    assert active["max"] == 2


def test_make_jury_fn_requires_unanimous_pass(monkeypatch):
    verdicts = iter(
        [
            "REVIEW_VERDICT: PASS\nEvidence: ok",
            "REVIEW_VERDICT: FAIL\nIssues: bad",
        ]
    )

    def fake_l3(*args, **kwargs):
        return next(verdicts)

    monkeypatch.setattr(jury_module, "run_l3_test_engineer", fake_l3)

    jury_fn = jury_module.make_jury_fn("key", "model")
    result = jury_fn(
        {"problem_statement": "fix bug"},
        {"worker": "l4", "final_report": "L4_STATUS: READY_FOR_REVIEW\nSummary: done"},
    )

    assert result == {"jury_pass": False, "verified": True}
