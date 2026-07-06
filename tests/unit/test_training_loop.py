"""Orchestration tests for the eval runners and the evolver, using stub agents."""
import os
import tempfile

import pytest


def _grade_from_diff(task_id, diff):
    # Treat a diff containing "FIX" as resolving the hidden tests.
    return {"resolved": "FIX" in (diff or ""), "status": "graded"}


def test_eval_runners_with_stubs():
    from langbridge_code.training.evals import runner

    specs = [{"task_id": "t1"}, {"task_id": "t2"}]

    def coder_fn(spec):
        return {"diff": "FIX" if spec["task_id"] == "t1" else "noop\n+x", "turns": 2}

    rows = runner.eval_l4(specs, coder_fn=coder_fn, grade=_grade_from_diff)
    assert [r["gt_pass"] for r in rows] == [True, False]
    assert rows[1]["patch_lines"] == 1

    cases = [
        {"task_id": "t1", "case": "good", "gt_pass": True},
        {"task_id": "t1", "case": "bad", "gt_pass": False},
    ]
    rrows = runner.eval_l3(cases, review_fn=lambda c: {"approved": c["gt_pass"]})
    assert all(r["approved"] == r["gt_pass"] for r in rrows)

    def loop_fn(spec):
        return {
            "task": spec["task_id"], "worker": "l4",
            "rounds": [{"round": 1, "diff": "FIX", "approved": True, "comments": ""}],
            "approved": True, "final_diff": "FIX",
        }

    lrows, traces = runner.eval_loop(specs, loop_fn=loop_fn, grade=_grade_from_diff)
    assert all(r["gt_pass"] for r in lrows)
    assert traces[0]["labels"]["gt_pass"] is True


def test_evolver_accepts_improving_change():
    from langbridge_code import policy
    from langbridge_code.training import evolver

    with tempfile.TemporaryDirectory() as d:
        os.environ["LANGBRIDGE_POLICY_DIR"] = d
        try:
            # The implementer only produces a passing FIX once the policy carries
            # the learned guidance — simulating a genuine improvement so the gate
            # should accept the change.
            def loop_fn(spec):
                p = policy.load()
                learned = any("surgical fix" in b for b in p["l4"]["guidance"])
                diff = "FIX" if learned else "noop"
                return {
                    "task": spec["task_id"], "worker": "l4",
                    "rounds": [{"round": 1, "diff": diff, "approved": True, "comments": ""}],
                    "approved": True, "final_diff": diff,
                }

            def evolve_fn(prompt):
                return {"diagnosis": "implementer flailing",
                        "l4_guidance_add": ["Make a surgical fix to the failing function."]}

            results = evolver.run(
                [{"task_id": "t1"}, {"task_id": "t2"}],
                loop_fn=loop_fn, grade=_grade_from_diff, evolve_fn=evolve_fn,
                epochs=1, batch_size=2, do_gate=True, checkpoint_every="batch",
            )
            assert len(results) == 1
            res = results[0]
            assert res["accepted"] is True
            assert res["new_total"] > res["old_total"]
            p = policy.load()
            assert any("surgical fix" in b for b in p["l4"]["guidance"])
            assert policy.list_checkpoints()  # a checkpoint was written
        finally:
            del os.environ["LANGBRIDGE_POLICY_DIR"]


def test_evolver_rolls_back_non_improving_change():
    from langbridge_code import policy
    from langbridge_code.training import evolver

    with tempfile.TemporaryDirectory() as d:
        os.environ["LANGBRIDGE_POLICY_DIR"] = d
        try:
            # Outcome never depends on the policy, so the change can't improve the
            # score and must be rolled back.
            def loop_fn(spec):
                return {
                    "task": spec["task_id"], "worker": "l4",
                    "rounds": [{"round": 1, "diff": "noop", "approved": True, "comments": ""}],
                    "approved": True, "final_diff": "noop",
                }

            def evolve_fn(prompt):
                return {"l4_guidance_add": ["Some guidance that does nothing useful."]}

            results = evolver.run(
                [{"task_id": "t1"}, {"task_id": "t2"}],
                loop_fn=loop_fn, grade=_grade_from_diff, evolve_fn=evolve_fn,
                epochs=1, batch_size=2, do_gate=True, checkpoint_every="batch",
            )
            res = results[0]
            assert res["accepted"] is False
            p = policy.load()
            # rolled back: the guidance was not kept
            assert p["l4"]["guidance"] == []
        finally:
            del os.environ["LANGBRIDGE_POLICY_DIR"]


def test_reviewer_guidance_needs_anchor_in_evolver():
    from langbridge_code import policy
    from langbridge_code.training import evolver

    with tempfile.TemporaryDirectory() as d:
        os.environ["LANGBRIDGE_POLICY_DIR"] = d
        try:
            # No grade -> no anchor -> l3 guidance must be skipped.
            def loop_fn(spec):
                return {"task": spec["task_id"], "worker": "l4",
                        "rounds": [{"round": 1, "diff": "x", "approved": True, "comments": ""}],
                        "approved": True, "final_diff": "x"}

            def grade_unknown(task_id, diff):
                return {"resolved": False, "status": "no_spec"}

            def evolve_fn(prompt):
                return {"l3_guidance_add": ["Be much stricter."]}

            evolver.run([{"task_id": "t1"}], loop_fn=loop_fn, grade=grade_unknown,
                        evolve_fn=evolve_fn, jury_fn=None, epochs=1, batch_size=1,
                        do_gate=False)
            p = policy.load()
            assert p["l3"]["guidance"] == []
        finally:
            del os.environ["LANGBRIDGE_POLICY_DIR"]
