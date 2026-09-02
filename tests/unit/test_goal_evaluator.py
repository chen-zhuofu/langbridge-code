from unittest.mock import MagicMock, patch

from langbridge_code.agents.goal_evaluator import (
    EVALUATOR_TOOL_SCHEMAS,
    REVIEWER_TOOL_SCHEMAS,
    GoalEvaluatorAgent,
    GoalVerdict,
    ReviewVerdict,
    _parse_review_verdict,
    _parse_verdict,
)
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


def test_reviewer_tool_schemas_add_ask_user_but_no_write_path():
    evaluator_names = {schema["name"] for schema in EVALUATOR_TOOL_SCHEMAS}
    reviewer_names = {schema["name"] for schema in REVIEWER_TOOL_SCHEMAS}
    # Existing goal evaluator behavior is unchanged: it still has write/Edit.
    assert "write" in evaluator_names
    assert "Edit" in evaluator_names
    assert "ask_user" not in evaluator_names
    # Reviewer mode gets ask_user but no file-write schema at all.
    assert "ask_user" in reviewer_names
    assert "write" not in reviewer_names
    assert "Edit" not in reviewer_names


def test_reviewer_run_tool_refuses_write_even_if_hallucinated():
    from langbridge_code.agents.goal_evaluator import REVIEWER_TOOL_NAMES

    agent = GoalEvaluatorAgent("key", "model")
    call = {"name": "write", "call_id": "c1", "arguments": '{"path": "x", "content": "y"}'}

    output = agent._run_tool(call, [], allowed_tool_names=REVIEWER_TOOL_NAMES)
    assert "Tool error" in output["output"]
    assert "write" in output["output"]


def test_parse_review_verdict_release():
    parsed = _parse_review_verdict("RELEASE\nTests pass and the CLI output matches the request.")
    assert parsed.release is True
    assert "CLI output" in parsed.note


def test_parse_review_verdict_continue():
    parsed = _parse_review_verdict("CONTINUE\nRun the failing test and fix the assertion.")
    assert parsed.release is False
    assert "failing test" in parsed.next_prompt


def test_parse_review_verdict_continue_without_prompt_fails_closed():
    parsed = _parse_review_verdict("CONTINUE\n")
    assert parsed.release is False
    assert parsed.next_prompt != ""


def test_parse_review_verdict_empty_text_fails_closed():
    parsed = _parse_review_verdict("")
    assert parsed.release is False
    assert parsed.next_prompt != ""


def test_parse_review_verdict_unparseable_text_fails_closed():
    parsed = _parse_review_verdict("I think this looks fine, ship it.")
    assert parsed.release is False
    assert parsed.next_prompt != ""


def test_parse_review_verdict_json_without_explicit_release_true_fails_closed():
    parsed = _parse_review_verdict('{"release": false, "next_prompt": ""}')
    assert parsed.release is False
    assert parsed.next_prompt != ""


def test_reviewer_review_sends_original_request_verbatim():
    agent = GoalEvaluatorAgent("key", "model")
    fake = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "RELEASE\nLooks correct."}],
            }
        ]
    }
    captured = {}

    def fake_create(api_key, model, messages, **kwargs):
        captured["messages"] = messages
        return fake

    original_request = "Please rename `foo` to `bar` — keep the trailing \"quotes\" intact."
    with patch("langbridge_code.agents.goal_evaluator.create_model_response", side_effect=fake_create):
        verdict = agent.review(original_request, [{"role": "assistant", "content": "done"}])

    assert verdict.release is True
    assert original_request in captured["messages"][1]["content"]


def test_reviewer_review_max_steps_without_verdict_fails_closed():
    agent = GoalEvaluatorAgent("key", "model")
    tool_call_response = {
        "output": [
            {"type": "function_call", "name": "glob", "call_id": "c1", "arguments": "{}"}
        ]
    }
    with patch("langbridge_code.agents.goal_evaluator.GOAL_EVALUATOR_MAX_STEPS", 1):
        with patch(
            "langbridge_code.agents.goal_evaluator.create_model_response",
            return_value=tool_call_response,
        ):
            verdict = agent.review("do it", [{"role": "assistant", "content": "x"}])
    assert verdict.release is False
    assert verdict.next_prompt != ""


def test_reviewer_label_is_reviewer_not_goal_evaluator():
    agent = GoalEvaluatorAgent("key", "model")
    fake = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "RELEASE\nOk."}]}
        ]
    }
    captured = {}

    def fake_create(api_key, model, messages, **kwargs):
        captured["label"] = kwargs.get("label")
        return fake

    with patch("langbridge_code.agents.goal_evaluator.create_model_response", side_effect=fake_create):
        agent.review("do it", [{"role": "assistant", "content": "x"}])

    assert captured["label"] == "Reviewer"


def test_reviewer_ask_user_dispatches_to_question_callback():
    agent = GoalEvaluatorAgent("key", "model")
    call = {
        "name": "ask_user",
        "call_id": "c1",
        "arguments": '{"description": "d", "question": "Which target?", "options": ["a", "b", "c"]}',
    }
    received = {}

    def fake_question(question, options):
        received["question"] = question
        received["options"] = options
        return "a"

    output = agent._run_tool(call, [], question_callback=fake_question)
    assert received["question"] == "Which target?"
    assert output["output"] == "The user answered:\na"


def test_run_reviewer_loop_continues_until_released(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    session = MainAgentSession(
        "key",
        "model",
        [{"role": "system", "content": "test"}],
        run_log,
        turn_id=1,
    )
    session.send = MagicMock(side_effect=["draft reply", "final reply"])
    evaluator = MagicMock()
    evaluator.review.side_effect = [
        ReviewVerdict(release=False, next_prompt="Add the missing test case."),
        ReviewVerdict(release=True, note="Matches the original request."),
    ]

    with patch("langbridge_code.agents.main_agent.GoalEvaluatorAgent", return_value=evaluator):
        reply = session.run_reviewer_loop("Fix the bug and add a test")

    assert reply == "final reply"
    assert session.send.call_count == 2
    assert evaluator.review.call_count == 2
    second_prompt = session.send.call_args_list[1].args[0]
    assert second_prompt == "Add the missing test case."
    first_request = evaluator.review.call_args_list[0].args[0]
    second_request = evaluator.review.call_args_list[1].args[0]
    assert first_request == "Fix the bug and add a test"
    assert second_request == "Fix the bug and add a test"


def test_run_reviewer_loop_stops_at_round_cap(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    session = MainAgentSession("key", "model", [{"role": "system", "content": "x"}], run_log, 1)
    session.send = MagicMock(return_value="not done")
    evaluator = MagicMock()
    evaluator.review.return_value = ReviewVerdict(release=False, next_prompt="Keep going.")

    with patch("langbridge_code.agents.main_agent.GoalEvaluatorAgent", return_value=evaluator):
        with patch("langbridge_code.agents.main_agent.GOAL_DEFAULT_MAX_TURNS", 2):
            reply = session.run_reviewer_loop("Do the thing")

    assert reply == "not done"
    assert session.send.call_count == 2
