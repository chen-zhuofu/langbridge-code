from unittest.mock import patch

from langbridge_code.agents.goal_evaluator import GoalEvaluatorAgent
from langbridge_code.tools import GOAL_VERIFICATION_TOOL_NAMES, MAIN_TOOL_NAMES
from langbridge_code.util.goal import SessionGoal, build_continuation_prompt


def test_goal_verification_tools_exclude_stateful_main_tools():
    # The evaluator verifies only; it must not merge, browse, or change schedules.
    assert GOAL_VERIFICATION_TOOL_NAMES == MAIN_TOOL_NAMES - {
        "merge_branch",
        "browser",
        "schedule",
    }


def test_evaluator_prompt_and_tools_include_memory_writer():
    from langbridge_code.agents.goal_evaluator import EVALUATOR_PROMPT, EVALUATOR_TOOL_SCHEMAS

    assert "memory_writer" in EVALUATOR_PROMPT
    assert "python3" in EVALUATOR_PROMPT
    assert any(schema["name"] == "memory_writer" for schema in EVALUATOR_TOOL_SCHEMAS)


def test_build_continuation_prompt_includes_guidance():
    goal = SessionGoal(
        condition="tests pass",
        last_reason="pytest still reports 2 failures",
        last_guidance="Run pytest tests/unit and fix the failures.",
    )
    prompt = build_continuation_prompt(goal)
    assert "pytest still reports 2 failures" in prompt
    assert "Run pytest tests/unit" in prompt
    assert "Do not reply with a final summary yet" in prompt


def test_evaluator_agent_parses_met_response():
    goal = SessionGoal(condition="tests pass")
    messages = [
        {"role": "user", "content": "run tests"},
        {"role": "assistant", "content": "All tests passed."},
    ]
    fake_response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            "PASS\n"
                            "Transcript shows pytest exited 0 with all tests passing."
                        ),
                    }
                ],
            }
        ]
    }
    agent = GoalEvaluatorAgent("key", "model")
    with patch("langbridge_code.agents.goal_evaluator.create_model_response", return_value=fake_response):
        verdict = agent.evaluate(goal.condition, messages)
    assert verdict.met is True
    assert "passing" in verdict.reason.lower()


def test_evaluator_uses_web_tools_before_verdict():
    messages = [{"role": "assistant", "content": "Deployed to https://example.com"}]
    tool_response = {
        "output": [
            {
                "type": "function_call",
                "name": "read_webpage",
                "call_id": "call_1",
                "arguments": '{"description":"check deploy","url":"https://example.com"}',
            }
        ]
    }
    pass_response = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "PASS\nLive page shows the app."}],
            }
        ]
    }
    agent = GoalEvaluatorAgent("key", "model")
    with patch(
        "langbridge_code.agents.goal_evaluator.create_model_response",
        side_effect=[tool_response, pass_response],
    ) as mock_create:
        with patch.dict(
            "langbridge_code.agents.goal_evaluator.GOAL_VERIFICATION_TOOLS",
            {"read_webpage": lambda **_: '{"title":"Example","text":"Hello"}'},
        ):
            verdict = agent.evaluate("site is live at example.com", messages)
    assert verdict.met is True
    assert mock_create.call_count == 2


def test_evaluator_can_run_bash_for_verification():
    messages = [{"role": "assistant", "content": "Tests should pass."}]
    tool_response = {
        "output": [
            {
                "type": "function_call",
                "name": "bash",
                "call_id": "call_1",
                "arguments": '{"description":"run tests","command":"pytest -q"}',
            }
        ]
    }
    pass_response = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "PASS\npytest exited 0."}],
            }
        ]
    }
    agent = GoalEvaluatorAgent("key", "model")
    with patch(
        "langbridge_code.agents.goal_evaluator.create_model_response",
        side_effect=[tool_response, pass_response],
    ) as mock_create:
        with patch.dict(
            "langbridge_code.agents.goal_evaluator.GOAL_VERIFICATION_TOOLS",
            {"bash": lambda **_: "3 passed"},
        ):
            verdict = agent.evaluate("all tests pass", messages)
    assert verdict.met is True
    assert mock_create.call_args_list[0].kwargs["tool_schemas"] == mock_create.call_args_list[1].kwargs["tool_schemas"]
    tool_names = {schema["name"] for schema in mock_create.call_args_list[0].kwargs["tool_schemas"]}
    assert "bash" in tool_names
    assert "read_file" in tool_names
    assert "read_plan" not in tool_names
