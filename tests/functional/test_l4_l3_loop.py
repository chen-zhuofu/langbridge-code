from langbridge_cli.agent import append_pm_l3_review

READY = "L4_STATUS: READY_FOR_REVIEW\nSummary: implemented"
PASS = "REVIEW_VERDICT: PASS\nEvidence: tests pass"
NEEDS_WORK = "REVIEW_VERDICT: NEEDS_WORK\nIssues: missing edge case"


def test_loop_passes_on_first_review_without_retrying_l4(monkeypatch):
    l3_calls = []

    def fake_l3(api_key, model, task, context):
        l3_calls.append(task)
        return PASS

    def fake_l4(*args, **kwargs):
        raise AssertionError("L4 should not be re-run when L3 passes first review")

    monkeypatch.setattr("langbridge_cli.multi_agent.run_l3_test_engineer", fake_l3)
    monkeypatch.setattr("langbridge_cli.multi_agent.run_l4_engineer", fake_l4)

    output = append_pm_l3_review("key", "model", {"task": "build", "context": "repo"}, READY)

    assert "PM_REVIEW_STATUS: OK" in output
    assert l3_calls == ["build"]


def test_loop_retries_l4_until_l3_passes(monkeypatch):
    verdicts = iter([NEEDS_WORK, PASS])
    l4_feedback = []

    def fake_l3(api_key, model, task, context):
        return next(verdicts)

    def fake_l4(api_key, model, task, context, feedback):
        l4_feedback.append(feedback)
        return "L4_STATUS: READY_FOR_REVIEW\nSummary: fixed the edge case"

    monkeypatch.setattr("langbridge_cli.multi_agent.run_l3_test_engineer", fake_l3)
    monkeypatch.setattr("langbridge_cli.multi_agent.run_l4_engineer", fake_l4)

    output = append_pm_l3_review("key", "model", {"task": "build", "context": "repo"}, READY)

    assert "PM_REVIEW_STATUS: OK" in output
    assert "fixed the edge case" in output
    assert len(l4_feedback) == 1
    assert "NEEDS_WORK" in l4_feedback[0]


def test_loop_gives_up_after_max_turns(monkeypatch):
    l3_calls = []
    l4_calls = []

    def fake_l3(api_key, model, task, context):
        l3_calls.append(task)
        return NEEDS_WORK

    def fake_l4(api_key, model, task, context, feedback):
        l4_calls.append(feedback)
        return READY

    monkeypatch.setattr("langbridge_cli.multi_agent.run_l3_test_engineer", fake_l3)
    monkeypatch.setattr("langbridge_cli.multi_agent.run_l4_engineer", fake_l4)
    monkeypatch.setattr("langbridge_cli.agent.MAX_L4_L3_TURNS", 2)

    output = append_pm_l3_review("key", "model", {"task": "build", "context": "repo"}, READY)

    assert "PM_REVIEW_STATUS: NEEDS_WORK" in output
    assert len(l3_calls) == 2
    assert len(l4_calls) == 2


def test_loop_stops_when_l4_can_no_longer_deliver(monkeypatch):
    l4_calls = []

    def fake_l3(api_key, model, task, context):
        return NEEDS_WORK

    def fake_l4(api_key, model, task, context, feedback):
        l4_calls.append(feedback)
        return "L4_STATUS: BLOCKED\nSummary: cannot proceed"

    monkeypatch.setattr("langbridge_cli.multi_agent.run_l3_test_engineer", fake_l3)
    monkeypatch.setattr("langbridge_cli.multi_agent.run_l4_engineer", fake_l4)

    output = append_pm_l3_review("key", "model", {"task": "build", "context": "repo"}, READY)

    assert "PM_REVIEW_STATUS: NEEDS_WORK" in output
    assert output.startswith("L4_STATUS: BLOCKED")
    assert len(l4_calls) == 1


def test_worklog_records_negotiation(tmp_path, monkeypatch):
    def fake_l3(api_key, model, task, context, **kwargs):
        return PASS

    monkeypatch.setattr("langbridge_cli.multi_agent.run_l3_test_engineer", fake_l3)

    run_log = tmp_path / "run.json"
    output = append_pm_l3_review(
        "key",
        "model",
        {"task": "build", "context": "repo"},
        READY,
        run_log_path=run_log,
    )

    worklog = tmp_path / "run.worklog.md"
    assert worklog.exists()
    text = worklog.read_text(encoding="utf-8")
    assert "L4<->L3 negotiation: build" in text
    assert "WORKLOG_TOKEN: ready" in text
    assert "WORKLOG_TOKEN: pass" in text
    assert "PM_REVIEW_STATUS: OK" in output
