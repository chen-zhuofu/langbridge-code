"""Unit tests for the training core (pure logic, no LLM / no API)."""
import os
import tempfile

import pytest


def test_l3_cases_from_specs():
    from langbridge_code.training.l3_cases import l3_cases_from_specs

    specs = [{"task_id": "t1", "base_commit": "abc", "problem_statement": "fix bug",
               "gold_code_patch": "FIX", "test_patch": "tests", "test_files": ["t.py"]}]

    def grade(task_id, diff):
        return {"resolved": "FIX" in (diff or "")}

    cases = l3_cases_from_specs(specs, grade)
    assert len(cases) == 2
    gold = next(c for c in cases if c["case"] == "gold")
    bad = next(c for c in cases if c["case"] == "no_fix")
    assert gold["gt_pass"] is True and gold["diff"] == "FIX"
    assert bad["gt_pass"] is False and bad["diff"] == ""


    from langbridge_code.training import metrics

    l4_rows = [
        {"task_id": "a", "gt_pass": True, "turns": 3, "patch_lines": 10},
        {"task_id": "b", "gt_pass": False, "turns": 5, "patch_lines": 0},
    ]
    m = metrics.compute_metrics("l4", l4_rows)
    assert m["gt_pass_rate"] == 0.5
    assert m["empty_patch_rate"] == 0.5
    assert m["avg_turns"] == 4.0

    l3_rows = [
        {"approved": True, "gt_pass": True},    # tp
        {"approved": True, "gt_pass": False},   # fp -> false approval
        {"approved": False, "gt_pass": True},   # fn -> false rejection
        {"approved": False, "gt_pass": False},  # tn
    ]
    r = metrics.compute_metrics("l3", l3_rows)
    assert r["accuracy"] == 0.5
    assert r["false_approval_rate"] == 0.5
    assert r["false_rejection_rate"] == 0.5
    assert r["confusion"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}

    loop_rows = [
        {"rounds": 1, "approved": True, "gt_pass": True},
        {"rounds": 2, "approved": True, "gt_pass": False},   # reward hack
        {"rounds": 3, "approved": False, "gt_pass": True},   # false block
    ]
    lp = metrics.compute_metrics("loop", loop_rows)
    assert lp["reward_hack_rate"] == round(1 / 3, 3)
    assert lp["false_block_rate"] == round(1 / 3, 3)
    assert lp["first_pass_rate"] == round(1 / 3, 3)


def test_metrics_pm_and_l5():
    from langbridge_code.training import metrics

    pm_rows = [
        {"completed": True, "gt_pass": True, "component_tasks": 3, "l5_fraction": 0.0},
        {"completed": True, "gt_pass": False, "component_tasks": 5, "l5_fraction": 0.5},
    ]
    m = metrics.compute_metrics("pm", pm_rows)
    assert m["completion_rate"] == 1.0
    assert m["gt_pass_rate"] == 0.5
    assert m["reward_hack_rate"] == 0.5  # completed but tests fail

    l5_rows = [{"gt_pass": True, "turns": 8, "patch_lines": 40, "subtasks": 3, "subtasks_done": 3}]
    m5 = metrics.compute_metrics("l5", l5_rows)
    assert m5["avg_subtasks"] == 3.0


def test_record_and_leaderboard_roundtrip():
    from langbridge_code.training import metrics

    with tempfile.TemporaryDirectory() as d:
        os.environ["LANGBRIDGE_EVAL_RESULTS_DIR"] = d
        try:
            path = metrics.record_result(
                "l4",
                [{"task_id": "a", "gt_pass": True, "turns": 2, "patch_lines": 5}],
                model="stub", dataset="test", policy_version=1,
            )
            assert os.path.exists(path)
            runs = metrics.load_results("l4")
            assert len(runs) == 1 and runs[0]["metrics"]["gt_pass_rate"] == 1.0
            board = metrics.build_leaderboard()
            assert "## l4" in board and "gt_pass_rate" in board
        finally:
            del os.environ["LANGBRIDGE_EVAL_RESULTS_DIR"]


def test_signals_responsiveness_alignment_calibration():
    from langbridge_code.training import signals

    trace = {
        "rounds": [
            {"round": 1, "diff": "old", "approved": False, "comments": "add a test"},
            {"round": 2, "diff": "new", "approved": True, "comments": ""},
        ],
        "approved": True,
        "labels": {"gt_pass": False, "reward_hack": True, "false_block": False, "source": "tests"},
    }
    resp = signals.responsiveness(trace)
    assert resp["score"] == 1.0  # diff changed after the change-request

    # alignment with a judge that always says yes
    al = signals.alignment(trace, judge=lambda c, b, a: True)
    assert al["score"] == 1.0
    # no judge -> None
    assert signals.alignment(trace)["score"] is None

    # calibration: approved but gt fail -> too_lenient
    assert signals.calibration(trace) == "too_lenient"


def test_signals_batch_patterns():
    from langbridge_code.training import signals

    traces = [
        {"rounds": [{"approved": False, "comments": ""}], "labels": {"reward_hack": True}},
        {"rounds": [{"approved": False, "comments": ""}], "labels": {"reward_hack": True}},
    ]
    flags = signals.batch_patterns(traces, min_tasks=2)
    names = {f["pattern"] for f in flags}
    assert "reviewer_silence" in names
    assert "reward_hack" in names


def test_gate_apply_proposal_reviewer_anchor():
    from langbridge_code import policy
    from langbridge_code.training import gate

    with tempfile.TemporaryDirectory() as d:
        os.environ["LANGBRIDGE_POLICY_DIR"] = d
        try:
            p = policy.load()
            proposal = {
                "diagnosis": "x",
                "l4_guidance_add": ["Run the whole test file."],
                "l3_guidance_add": ["Be stricter about missing tests."],
            }
            # No anchor -> l3 change skipped, l4 applied.
            ch = gate.apply_proposal(p, proposal, allow_reviewer=False)
            assert any("test file" in b for b in p["l4"]["guidance"])
            assert p["l3"]["guidance"] == []
            assert "skipped" in ch

            # With anchor -> l3 applied.
            ch2 = gate.apply_proposal(p, proposal, allow_reviewer=True)
            assert any("stricter" in b for b in p["l3"]["guidance"])
        finally:
            del os.environ["LANGBRIDGE_POLICY_DIR"]


def test_gate_strips_oracle_leaks():
    from langbridge_code import policy
    from langbridge_code.training import gate

    with tempfile.TemporaryDirectory() as d:
        os.environ["LANGBRIDGE_POLICY_DIR"] = d
        try:
            p = policy.load()
            proposal = {"l4_guidance_add": [
                "Make the hidden tests pass.",        # leak -> dropped
                "Keep changes surgical and focused.",  # kept
            ]}
            gate.apply_proposal(p, proposal, allow_reviewer=True)
            joined = " ".join(p["l4"]["guidance"])
            assert "surgical" in joined
            assert "hidden tests" not in joined
        finally:
            del os.environ["LANGBRIDGE_POLICY_DIR"]


def test_gate_scoring_and_acceptance():
    from langbridge_code.training import gate

    assert gate.sample_score(True, True) == 0
    assert gate.sample_score(True, False) == -3   # reward hack worst
    assert gate.sample_score(False, True) == -1
    assert gate.sample_score(False, False) == -2

    old = [{"approved": True, "passed": False}]   # -3
    new = [{"approved": True, "passed": True}]    # 0
    ok, ot, nt = gate.accept_change(old, new)
    assert ok and ot == -3 and nt == 0
