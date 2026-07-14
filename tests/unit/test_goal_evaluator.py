from unittest.mock import MagicMock, patch

from langbridge_code.agents.goal_evaluator import GoalEvaluatorAgent, GoalVerdict, _parse_verdict
from langbridge_code.agents.main_agent import MainAgentSession
from langbridge_code.util.goal import STATUS_ACHIEVED, STATUS_ACTIVE, SessionGoal, new_goal


def test_goal_evaluator_parses_verdict_with_guidance():
    agent = GoalEvaluatorAgent("key", "model")
    fake = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            "NEEDS_WORK\n"
                            "- No test output shown in the transcript.\n"
                            "- Run pytest and paste the result."
                        ),
                    }
                ],
            }
        ]
    }
    with patch("langbridge_code.agents.goal_evaluator.create_model_response", return_value=fake):
        verdict = agent.evaluate("tests pass", [{"role": "assistant", "content": "done"}])
    assert verdict.met is False
    assert "test output" in verdict.guidance.lower()
    assert "pytest" in verdict.guidance.lower()


def test_goal_evaluator_parses_pass_verdict():
    parsed = _parse_verdict("PASS\nTranscript shows clean test run.")
    assert parsed.met is True
    assert "clean test run" in parsed.reason


def test_goal_evaluator_parses_needs_work_verdict():
    parsed = _parse_verdict("NEEDS_WORK\n- Fix auth test\n- Rerun pytest")
    assert parsed.met is False
    assert "Fix auth test" in parsed.guidance


def test_run_goal_loop_continues_until_evaluator_says_met(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    goal = new_goal("tests pass", max_turns=5)
    messages = [{"role": "system", "content": "test"}]
    session = MainAgentSession(
        "key",
        "model",
        messages,
        run_log,
        turn_id=1,
    )
    session.send = MagicMock(side_effect=["still working", "all tests passed"])
    evaluator = MagicMock()
    evaluator.evaluate.side_effect = [
        GoalVerdict(met=False, reason="no evidence", guidance="run tests"),
        GoalVerdict(met=True, reason="transcript shows passing tests"),
    ]

    with patch("langbridge_code.agents.main_agent.GoalEvaluatorAgent", return_value=evaluator):
        reply, result = session.run_goal_loop(goal, initial_prompt="tests pass")

    assert reply == "all tests passed"
    assert result.status == STATUS_ACHIEVED
    assert session.send.call_count == 2
    assert evaluator.evaluate.call_count == 2
    second_prompt = session.send.call_args_list[1].args[0]
    assert "NOT met" in second_prompt
    assert "run tests" in second_prompt


def test_run_goal_loop_stops_at_turn_limit(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    goal = SessionGoal(condition="tests pass", max_turns=1, status=STATUS_ACTIVE)
    session = MainAgentSession("key", "model", [{"role": "system", "content": "x"}], run_log, 1)
    session.send = MagicMock(return_value="not done")
    evaluator = MagicMock()
    evaluator.evaluate.return_value = GoalVerdict(met=False, reason="still failing", guidance="retry")

    with patch("langbridge_code.agents.main_agent.GoalEvaluatorAgent", return_value=evaluator):
        _, result = session.run_goal_loop(goal, initial_prompt="tests pass")

    assert result.status != STATUS_ACHIEVED
    assert session.send.call_count == 1
