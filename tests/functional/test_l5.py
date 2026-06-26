from langbridge_cli.agents.agent import run_l5_component
from langbridge_cli.agents.component_plan import (
    next_unfinished_index,
    parse_sub_tasks,
    render_component_plan,
    slugify,
    write_component_plan,
)

READY = "L5_STATUS: READY_FOR_REVIEW\nSummary: implemented the sub-task"
PUSH_BACK = "L5_STATUS: PUSH_BACK\nRationale: the failing test asserts behavior the task never required"
PASS = "REVIEW_VERDICT: PASS\nEvidence: tests pass"
NEEDS_WORK = "REVIEW_VERDICT: NEEDS_WORK\nIssues: missing edge case"


def _use_tmp_plans(monkeypatch, tmp_path):
    monkeypatch.setattr("langbridge_cli.agents.component_plan.COMPONENT_PLAN_DIR", tmp_path)


def test_component_plan_round_trip():
    content = render_component_plan("task", [("build core", False), ("integration test", True)])
    assert parse_sub_tasks(content) == [("build core", False), ("integration test", True)]


def test_next_unfinished_index():
    assert next_unfinished_index([("a", True), ("b", False)]) == 1
    assert next_unfinished_index([("a", True), ("b", True)]) is None


def test_slugify_is_filesystem_safe_and_unique():
    assert slugify("Build the Thing!") == "build-the-thing"
    assert slugify("Build the Thing?") == slugify("Build the Thing!")


def test_l5_delivers_when_every_sub_task_passes(tmp_path, monkeypatch):
    _use_tmp_plans(monkeypatch, tmp_path)
    plan = "- [ ] build core\n- [ ] integration test"
    calls = {"l5": 0}

    def fake_l5(api_key, model, task, context, feedback="", **kwargs):
        calls["l5"] += 1
        return plan if calls["l5"] == 1 else READY

    def fake_l3(api_key, model, task, context, **kwargs):
        return PASS

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l5_engineer", fake_l5)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l3_test_engineer", fake_l3)

    output = run_l5_component("key", "model", {"task": "hard feature", "context": "repo"})

    assert "PM_REVIEW_STATUS: OK" in output
    assert "2/2" in output
    plan_text = (tmp_path / f"{slugify('hard feature')}.md").read_text(encoding="utf-8")
    assert plan_text.count("- [x]") == 2


def test_l5_reuses_an_existing_plan_and_skips_planning(tmp_path, monkeypatch):
    _use_tmp_plans(monkeypatch, tmp_path)
    write_component_plan("hard feature", render_component_plan("hard feature", [("step a", True), ("step b", False)]))
    seen_tasks = []

    def fake_l5(api_key, model, task, context, feedback="", **kwargs):
        seen_tasks.append(task)
        return READY

    def fake_l3(api_key, model, task, context, **kwargs):
        return PASS

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l5_engineer", fake_l5)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l3_test_engineer", fake_l3)

    output = run_l5_component("key", "model", {"task": "hard feature", "context": "repo"})

    assert "PM_REVIEW_STATUS: OK" in output
    assert not any(task.startswith("Plan only") for task in seen_tasks)
    assert seen_tasks == ["step b"]


def test_l5_escalates_to_pm_when_a_sub_task_keeps_failing(tmp_path, monkeypatch):
    _use_tmp_plans(monkeypatch, tmp_path)
    plan = "- [ ] only step"
    calls = {"l5": 0}

    def fake_l5(api_key, model, task, context, feedback="", **kwargs):
        calls["l5"] += 1
        return plan if calls["l5"] == 1 else READY

    def fake_l3(api_key, model, task, context, **kwargs):
        return NEEDS_WORK

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l5_engineer", fake_l5)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l3_test_engineer", fake_l3)

    output = run_l5_component("key", "model", {"task": "hard feature"})

    assert "PM_REVIEW_STATUS: NEEDS_WORK" in output
    assert "escalating to PM" in output


def test_l5_push_back_goes_to_jury_and_passes(tmp_path, monkeypatch):
    _use_tmp_plans(monkeypatch, tmp_path / "plans")
    monkeypatch.setattr("langbridge_cli.config.L5_WORKLOG_DIR", tmp_path)
    l5_outputs = iter(["- [ ] only step", READY, PUSH_BACK])
    l3_outputs = iter([NEEDS_WORK, NEEDS_WORK, PASS, PASS])

    def fake_l5(api_key, model, task, context, feedback, **kwargs):
        return next(l5_outputs)

    def fake_l3(api_key, model, task, context, **kwargs):
        return next(l3_outputs)

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l5_engineer", fake_l5)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l3_test_engineer", fake_l3)

    run_log = tmp_path / "run.json"
    output = run_l5_component("key", "model", {"task": "hard feature"}, run_log_path=run_log)

    # The sub-task passes only because the 2-juror dispute cleared L5's push-back.
    assert "PM_REVIEW_STATUS: OK" in output
    worklog = (tmp_path / "l45_share_worklog.md").read_text(encoding="utf-8")
    assert "WORKLOG_TOKEN: push back" in worklog
    assert "Dispute jury" in worklog
    assert "DISPUTE_JURY_RESULT: PASS" in worklog


def test_l5_records_the_worklog(tmp_path, monkeypatch):
    _use_tmp_plans(monkeypatch, tmp_path / "plans")
    monkeypatch.setattr("langbridge_cli.config.L5_WORKLOG_DIR", tmp_path)
    plan = "- [ ] only step"
    calls = {"l5": 0}

    def fake_l5(api_key, model, task, context, feedback, **kwargs):
        calls["l5"] += 1
        return plan if calls["l5"] == 1 else READY

    def fake_l3(api_key, model, task, context, **kwargs):
        return PASS

    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l5_engineer", fake_l5)
    monkeypatch.setattr("langbridge_cli.agents.multi_agent.run_l3_test_engineer", fake_l3)

    run_log = tmp_path / "run.json"
    run_l5_component("key", "model", {"task": "hard feature"}, run_log_path=run_log)

    text = (tmp_path / "l45_share_worklog.md").read_text(encoding="utf-8")
    assert "L5 component: hard feature" in text
    assert "WORKLOG_TOKEN: ready" in text
    assert "WORKLOG_TOKEN: pass" in text
