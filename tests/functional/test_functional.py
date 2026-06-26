"""Functional tests that mock only the LLM boundary.

These exercise the real loop wiring (run_pm_loop -> run_agent -> run_tool_call ->
the L4/L3 specialist loops) and stub just the two functions that call the model:
agent.create_response (PM) and multi_agent.create_specialist_response (L4/L3).
"""

import copy
import json

from langbridge_cli.agents.agent import run_pm_loop, run_tool_call
from langbridge_cli.config import MAX_PM_SECONDS


def _message(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def _call(name, arguments, call_id="call_1"):
    return {"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(arguments)}


def _pm_responder(rounds):
    script = iter(rounds)

    def fake(*args, **kwargs):
        return {"output": next(script)}

    return fake


def _specialist_responder(by_label, counts):
    queues = {label: iter(items) for label, items in by_label.items()}

    def fake(*args, **kwargs):
        label = kwargs.get("label", args[4] if len(args) > 4 else None)
        counts[label] = counts.get(label, 0) + 1
        return {"output": [_message(next(queues[label]))]}

    return fake


def test_pm_loop_iterates_until_bug_status_none(tmp_path, monkeypatch):
    rounds = [
        [_message("Made progress on a subtask.\nBUG_STATUS: OPEN")],
        [_message("All subtasks done and e2e verify passed.\nBUG_STATUS: NONE")],
    ]
    calls = {"n": 0}

    def fake(*args, **kwargs):
        calls["n"] += 1
        return {"output": rounds[calls["n"] - 1]}

    monkeypatch.setattr("langbridge_cli.agents.agent.create_response", fake)

    finished = run_pm_loop("key", "model", "build a thing", tmp_path / "run.json", 1, print_reply=False)

    assert calls["n"] == 2
    assert finished.strip().splitlines()[-1] == "BUG_STATUS: NONE"


def test_pm_loop_stops_after_single_bug_status_none_round(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake(*args, **kwargs):
        calls["n"] += 1
        return {"output": [_message("Answered the question directly.\nBUG_STATUS: NONE")]}

    monkeypatch.setattr("langbridge_cli.agents.agent.create_response", fake)

    run_pm_loop("key", "model", "what is 2 + 2?", tmp_path / "run.json", 1, print_reply=False)

    assert calls["n"] == 1


def test_pm_update_plan_writes_todo_list(tmp_path, monkeypatch):
    todo_path = tmp_path / "todo_list.md"
    monkeypatch.setattr("langbridge_cli.tools.plan.TODO_LIST_PATH", todo_path)

    plan = "- [TODO] implement parser\n- [TODO] e2e test the parser\n"
    monkeypatch.setattr(
        "langbridge_cli.agents.agent.create_response",
        _pm_responder(
            [
                [_call("update_plan", {"content": plan, "purpose": "write the plan"})],
                [_message("Plan written.\nBUG_STATUS: NONE")],
            ]
        ),
    )

    run_pm_loop("key", "model", "build a parser", tmp_path / "run.json", 1, print_reply=False)

    assert todo_path.read_text(encoding="utf-8") == plan


def test_pm_delegates_to_l4_then_l3_passes(tmp_path, monkeypatch):
    counts = {}
    monkeypatch.setattr(
        "langbridge_cli.agents.multi_agent.create_specialist_response",
        _specialist_responder(
            {
                "L4 engineer": ["L4_STATUS: READY_FOR_REVIEW\nSummary: built it"],
                "L3 test engineer": ["REVIEW_VERDICT: PASS\nEvidence: tests pass"],
            },
            counts,
        ),
    )
    monkeypatch.setattr(
        "langbridge_cli.agents.agent.create_response",
        _pm_responder(
            [
                [_call("ask_l4_engineer", {"task": "build feature", "purpose": "delegate"})],
                [_message("Subtask verified.\nBUG_STATUS: NONE")],
            ]
        ),
    )

    run_log = tmp_path / "run.json"
    run_pm_loop(
        "key",
        "model",
        "build feature",
        run_log,
        1,
        approval_callback=lambda *args: True,
        print_reply=False,
    )

    log_text = run_log.read_text(encoding="utf-8")
    assert "L4_STATUS: READY_FOR_REVIEW" in log_text
    assert "REVIEW_VERDICT: PASS" in log_text
    assert "PM_REVIEW_STATUS: OK" in log_text
    assert counts == {"L4 engineer": 1, "L3 test engineer": 1}


def test_l3_needs_work_then_l4_fix_passes(tmp_path, monkeypatch):
    counts = {}
    monkeypatch.setattr(
        "langbridge_cli.agents.multi_agent.create_specialist_response",
        _specialist_responder(
            {
                "L4 engineer": [
                    "L4_STATUS: READY_FOR_REVIEW\nSummary: first attempt",
                    "L4_STATUS: READY_FOR_REVIEW\nSummary: fixed the edge case",
                ],
                "L3 test engineer": [
                    "REVIEW_VERDICT: NEEDS_WORK\nIssues: missing edge case",
                    "REVIEW_VERDICT: PASS\nEvidence: tests pass",
                ],
            },
            counts,
        ),
    )

    result = run_tool_call(
        _call("ask_l4_engineer", {"task": "build feature", "purpose": "delegate"}),
        api_key="key",
        model="model",
        approval_callback=lambda *args: True,
        run_log_path=tmp_path / "run.json",
        turn_id=1,
    )

    assert "PM_REVIEW_STATUS: OK" in result["output"]
    assert "fixed the edge case" in result["output"]
    assert counts == {"L4 engineer": 2, "L3 test engineer": 2}


def test_push_back_jury_both_pass_returns_ok(tmp_path, monkeypatch):
    counts = {}
    monkeypatch.setattr(
        "langbridge_cli.agents.multi_agent.create_specialist_response",
        _specialist_responder(
            {
                "L4 engineer": [
                    "L4_STATUS: READY_FOR_REVIEW\nSummary: built it",
                    "L4_STATUS: PUSH_BACK\nRationale: the test asserts behavior the task never required",
                ],
                "L3 test engineer": [
                    "REVIEW_VERDICT: NEEDS_WORK\nIssues: first review",
                    "REVIEW_VERDICT: NEEDS_WORK\nIssues: re-judge still disagrees",
                    "REVIEW_VERDICT: PASS\nEvidence: juror 1 verified",
                    "REVIEW_VERDICT: PASS\nEvidence: juror 2 verified",
                ],
            },
            counts,
        ),
    )

    result = run_tool_call(
        _call("ask_l4_engineer", {"task": "build feature", "purpose": "delegate"}),
        api_key="key",
        model="model",
        approval_callback=lambda *args: True,
        run_log_path=tmp_path / "run.json",
        turn_id=1,
    )

    assert "DISPUTE_JURY_RESULT: PASS" in result["output"]
    assert "PM_REVIEW_STATUS: OK" in result["output"]
    assert counts == {"L4 engineer": 2, "L3 test engineer": 4}


def test_push_back_jury_one_fail_returns_needs_work(tmp_path, monkeypatch):
    counts = {}
    monkeypatch.setattr(
        "langbridge_cli.agents.multi_agent.create_specialist_response",
        _specialist_responder(
            {
                "L4 engineer": [
                    "L4_STATUS: READY_FOR_REVIEW\nSummary: built it",
                    "L4_STATUS: PUSH_BACK\nRationale: I think the review is wrong",
                ],
                "L3 test engineer": [
                    "REVIEW_VERDICT: NEEDS_WORK\nIssues: first review",
                    "REVIEW_VERDICT: NEEDS_WORK\nIssues: re-judge still disagrees",
                    "REVIEW_VERDICT: PASS\nEvidence: juror 1 verified",
                    "REVIEW_VERDICT: FAIL\nIssues: juror 2 found a real bug",
                ],
            },
            counts,
        ),
    )

    result = run_tool_call(
        _call("ask_l4_engineer", {"task": "build feature", "purpose": "delegate"}),
        api_key="key",
        model="model",
        approval_callback=lambda *args: True,
        run_log_path=tmp_path / "run.json",
        turn_id=1,
    )

    assert "DISPUTE_JURY_RESULT: FAIL" in result["output"]
    assert "PM_REVIEW_STATUS: NEEDS_WORK" in result["output"]
    assert counts == {"L4 engineer": 2, "L3 test engineer": 4}


def test_pm_loop_stops_at_max_pm_loops(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.agents.agent.MAX_PM_LOOPS", 3)
    calls = {"n": 0}

    def fake(*args, **kwargs):
        calls["n"] += 1
        return {"output": [_message("still working\nBUG_STATUS: OPEN")]}

    monkeypatch.setattr("langbridge_cli.agents.agent.create_response", fake)

    run_pm_loop("key", "model", "never-ending task", tmp_path / "run.json", 1, print_reply=False)

    assert calls["n"] == 3


def test_pm_round_stops_at_max_agent_steps(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.agents.agent.MAX_AGENT_STEPS", 2)
    calls = {"n": 0}

    def fake(*args, **kwargs):
        calls["n"] += 1
        return {"output": [_call("list_dir", {"path": ".", "purpose": "keep looping"})]}

    monkeypatch.setattr("langbridge_cli.agents.agent.create_response", fake)

    finished = run_pm_loop("key", "model", "loops forever", tmp_path / "run.json", 1, print_reply=False)

    assert calls["n"] == 2
    assert "maximum tool-call steps" in finished


def test_pm_loop_stops_on_time_budget(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake(*args, **kwargs):
        calls["n"] += 1
        return {"output": [_message("still working\nBUG_STATUS: OPEN")]}

    monkeypatch.setattr("langbridge_cli.agents.agent.create_response", fake)
    monkeypatch.setattr(
        "langbridge_cli.agents.agent.over_time_budget",
        lambda start, max_seconds: max_seconds == MAX_PM_SECONDS,
    )

    run_pm_loop("key", "model", "slow task", tmp_path / "run.json", 1, print_reply=False)

    assert calls["n"] == 0


def test_todo_list_is_carried_into_the_next_round(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_cli.tools.plan.TODO_LIST_PATH", tmp_path / "todo_list.md")

    plan = "- [TODO] implement parser\n- [TODO] e2e test the parser\n"
    rounds = iter(
        [
            [_call("update_plan", {"content": plan, "purpose": "write the plan"})],
            [_message("Plan written.\nBUG_STATUS: OPEN")],
            [_message("All done and e2e verify passed.\nBUG_STATUS: NONE")],
        ]
    )
    seen_inputs = []

    def fake(*args, **kwargs):
        agent_input = args[2] if len(args) > 2 else kwargs["agent_input"]
        seen_inputs.append(copy.deepcopy(agent_input))
        return {"output": next(rounds)}

    monkeypatch.setattr("langbridge_cli.agents.agent.create_response", fake)

    run_pm_loop("key", "model", "build a parser", tmp_path / "run.json", 1, print_reply=False)

    second_round_input = seen_inputs[-1]
    user_messages = [m["content"] for m in second_round_input if m.get("role") == "user"]
    assert any("implement parser" in content for content in user_messages)
