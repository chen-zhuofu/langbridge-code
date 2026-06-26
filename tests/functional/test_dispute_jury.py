from langbridge_cli.agents.agent import run_l4_component

READY = "L4_STATUS: READY_FOR_REVIEW\nSummary: implemented"
PUSH_BACK = "L4_STATUS: PUSH_BACK\nRationale: the failing test asserts behavior the task never required"
PASS = "REVIEW_VERDICT: PASS\nEvidence: tests pass"
NEEDS_WORK = "REVIEW_VERDICT: NEEDS_WORK\nIssues: missing edge case"
FAIL = "REVIEW_VERDICT: FAIL\nIssues: real bug remains"


def _patch(monkeypatch, l3_verdicts, l4_response):
    # The first L4 turn (no feedback) is the build and always succeeds; later turns
    # (driven by L3 feedback) return the contested response under test.
    l3_calls = []
    verdicts = iter(l3_verdicts)
    l4_calls = []

    def fake_l3(api_key, model, task, context, **kwargs):
        l3_calls.append(context)
        return next(verdicts)

    def fake_l4(api_key, model, task, context, feedback="", **kwargs):
        if not feedback:
            return READY
        l4_calls.append(feedback)
        return l4_response

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l3_test_engineer", fake_l3)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l4_engineer", fake_l4)
    return l3_calls, l4_calls


def test_push_back_accepted_when_l3_concedes(monkeypatch):
    # initial concern -> L4 pushes back -> L3 re-judges and concedes (PASS)
    l3_calls, l4_calls = _patch(monkeypatch, [NEEDS_WORK, PASS], PUSH_BACK)

    output = run_l4_component("key", "model", {"task": "build", "context": "repo"})

    assert "PM_REVIEW_STATUS: OK" in output
    assert len(l3_calls) == 2  # initial review + re-judge, no jury
    assert len(l4_calls) == 1


def test_push_back_goes_to_jury_and_passes_when_unanimous(monkeypatch):
    # concern -> push back -> L3 insists (NEEDS_WORK) -> jury both PASS
    l3_calls, l4_calls = _patch(monkeypatch, [NEEDS_WORK, NEEDS_WORK, PASS, PASS], PUSH_BACK)

    output = run_l4_component("key", "model", {"task": "build", "context": "repo"})

    assert "PM_REVIEW_STATUS: OK" in output
    assert "DISPUTE_JURY_RESULT: PASS" in output
    assert len(l3_calls) == 4  # review + re-judge + 2 jurors


def test_push_back_fails_when_jury_not_unanimous(monkeypatch):
    # concern -> push back -> L3 insists -> jury splits (one FAIL) -> failure
    l3_calls, l4_calls = _patch(monkeypatch, [NEEDS_WORK, FAIL, PASS, FAIL], PUSH_BACK)

    output = run_l4_component("key", "model", {"task": "build", "context": "repo"})

    assert "PM_REVIEW_STATUS: NEEDS_WORK" in output
    assert "DISPUTE_JURY_RESULT: FAIL" in output
    assert len(l3_calls) == 4


def test_worklog_records_push_back_and_jury(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.config.L4_WORKLOG_DIR", tmp_path)
    verdicts = iter([NEEDS_WORK, NEEDS_WORK, PASS, PASS])

    def fake_l3(api_key, model, task, context, **kwargs):
        return next(verdicts)

    def fake_l4(api_key, model, task, context, feedback="", **kwargs):
        return READY if not feedback else PUSH_BACK

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l3_test_engineer", fake_l3)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l4_engineer", fake_l4)

    run_log = tmp_path / "run.json"
    run_l4_component("key", "model", {"task": "build", "context": "repo"}, run_log_path=run_log)

    text = (tmp_path / "l34_share_worklog.md").read_text(encoding="utf-8")
    assert "WORKLOG_TOKEN: push back" in text
    assert "Dispute jury" in text
    assert "WORKLOG_TOKEN: pass" in text
