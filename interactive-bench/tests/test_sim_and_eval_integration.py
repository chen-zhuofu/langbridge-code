"""Sim stop rules, scoring, and stub eval integration."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from harness.agent import make_stub_agent  # noqa: E402
from harness.score import score_episode  # noqa: E402
from harness.sim import (  # noqa: E402
    EpisodeState,
    SimDecision,
    apply_decision,
    run_episode,
    should_stop,
    timeout_sec,
)


def _spec(**kwargs):
    base = {
        "task_id": "demo",
        "instruction": "Do the thing",
        "intents": [
            {"id": "i1", "text": "Do the thing", "source_turn": 0, "revealed_at_start": True},
            {"id": "i2", "text": "Also verify", "source_turn": 1, "revealed_at_start": False},
        ],
        "baseline": {"agent_runtime_sec": 40, "prompt_count": 2},
        "sim": {
            "noop_message": "continue",
            "max_consecutive_noops": 4,
            "timeout_sec": 10_000,
            "session_analysis": "quiet then reveal",
        },
    }
    base.update(kwargs)
    return base


def test_timeout_is_max_40m_or_2x_runtime():
    assert timeout_sec(_spec()) == 10_000  # explicit sim.timeout_sec wins
    spec = _spec()
    del spec["sim"]["timeout_sec"]
    # baseline 40s → 2×40=80 < 40m floor → 2400
    assert timeout_sec(spec) == 2400.0
    spec["baseline"]["agent_runtime_sec"] = 2000
    assert timeout_sec(spec) == 4000.0


def test_episode_stops_after_four_noops():
    def agent_turn(_user: str):
        # Never done → sim no-ops while "still working" until the noop cap trips
        return {"assistant_text": "still implementing details", "done": False}

    episode = run_episode(_spec(), agent_turn, max_turns=20)
    assert episode["stop_reason"] == "max_consecutive_noops"
    assert episode["consecutive_noops_final"] >= 4
    # turn0 counts; continue messages do not
    assert episode["user_input_count"] == 1


def test_episode_timeout_stop():
    spec = _spec()
    spec["sim"]["timeout_sec"] = 0.05

    def agent_turn(_user: str):
        time.sleep(0.03)
        return {"assistant_text": "working", "done": False}

    episode = run_episode(spec, agent_turn, max_turns=50)
    assert episode["stop_reason"] == "timeout"


def test_score_compares_to_baseline():
    episode = {
        "user_input_count": 2,
        "interventions": 1,
        "elapsed_sec": 80.0,
        "revealed_intent_ids": ["i1", "i2"],
        "stop_reason": "agent_done",
    }
    scored = score_episode(_spec(), episode, tests_passed=True)
    assert scored["pass"] is True
    assert scored["intent_coverage"] == 1.0
    assert scored["runtime_ratio"] == 2.0  # 80/40
    assert scored["input_ratio"] == 1.0  # 2/2


def test_score_fail_without_tests():
    episode = {
        "user_input_count": 1,
        "interventions": 0,
        "elapsed_sec": 10,
        "revealed_intent_ids": ["i1", "i2"],
        "stop_reason": "agent_done",
    }
    scored = score_episode(_spec(), episode, tests_passed=False)
    assert scored["pass"] is False


def test_steer_resets_noop_streak():
    state = EpisodeState(consecutive_noops=3, revealed_intent_ids=["i1"])
    apply_decision(
        state,
        SimDecision(action="steer", message="Stay on login rate limit", counts_as_user_input=True),
        _spec()["intents"],
    )
    assert state.consecutive_noops == 0
    assert state.user_input_count == 1
    assert should_stop(state, _spec()) is None


def test_stub_agent_and_run_eval_cli(tmp_path, monkeypatch):
    from _lib import paths
    from eval import run_eval

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    out_dir = tmp_path / "eval_out"
    spec = _spec(task_id="cli-demo")
    (specs_dir / "cli-demo.json").write_text(json.dumps(spec), encoding="utf-8")

    monkeypatch.setattr(paths, "SPECS_DIR", specs_dir)
    monkeypatch.setattr(paths, "EVAL_OUT", out_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_eval.py", "--stub", "--task", "cli-demo"],
    )
    assert run_eval.main() == 0
    reports = list(out_dir.glob("*/report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["n"] == 1
    assert report["results"][0]["task_id"] == "cli-demo"


def test_run_eval_workers_and_offset(tmp_path, monkeypatch):
    from _lib import paths
    from eval import run_eval

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    out_dir = tmp_path / "eval_out"
    for tid in ("a-task", "b-task", "c-task"):
        (specs_dir / f"{tid}.json").write_text(
            json.dumps(_spec(task_id=tid)), encoding="utf-8"
        )

    monkeypatch.setattr(paths, "SPECS_DIR", specs_dir)
    monkeypatch.setattr(paths, "EVAL_OUT", out_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_eval.py", "--stub", "--workers", "2", "--offset", "1", "--limit", "2"],
    )
    assert run_eval.main() == 0
    report = json.loads(next(out_dir.glob("*/report.json")).read_text(encoding="utf-8"))
    assert report["workers"] == 2
    assert report["n"] == 2
    ids = {row["task_id"] for row in report["results"]}
    assert ids == {"b-task", "c-task"}


def test_make_stub_agent_finishes():
    agent = make_stub_agent(max_agent_turns=2)
    assert agent("hi")["done"] is False
    assert agent("continue")["done"] is True
