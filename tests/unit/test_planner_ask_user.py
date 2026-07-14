from langbridge_code.tools.ask_user import (
    ASK_USER_TOOL_SCHEMA,
    format_ask_user_choices,
    normalize_options,
    resolve_ask_user,
    resolve_ask_user_answer,
)
from langbridge_code.tools.agent_planner import PLANNER_TOOL_SCHEMAS
from langbridge_code.tools import MAIN_TOOL_SCHEMAS


def test_normalize_options_requires_three_assumptions():
    assert normalize_options(["a", "b", "c"]) == ["a", "b", "c"]
    try:
        normalize_options(["only one"])
    except ValueError as error:
        assert "exactly 3" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_format_ask_user_choices_shows_three_plus_other():
    text = format_ask_user_choices("Which tool?", ["CLI", "Web app", "Library"])
    assert "1. CLI" in text
    assert "2. Web app" in text
    assert "3. Library" in text
    assert "4. Other" in text


def test_resolve_ask_user_answer_maps_numbers_to_options():
    options = ["CLI", "Web app", "Library"]
    assert resolve_ask_user_answer("2", options) == "Web app"
    assert resolve_ask_user_answer("custom thing", options) == "custom thing"


def test_ask_user_and_update_plan_not_on_planner():
    names = {item["name"] for item in PLANNER_TOOL_SCHEMAS}
    assert "ask_user" not in names
    assert "update_plan" not in names


def test_ask_user_and_update_plan_on_main():
    names = {item["name"] for item in MAIN_TOOL_SCHEMAS}
    assert "update_plan" in names


def test_ask_user_schema_requires_options():
    assert "options" in ASK_USER_TOOL_SCHEMA["parameters"]["properties"]
    assert "question" in ASK_USER_TOOL_SCHEMA["parameters"]["required"]
    assert "options" in ASK_USER_TOOL_SCHEMA["parameters"]["required"]


def test_ask_user_returns_the_answer():
    out = resolve_ask_user(
        {
            "question": "Which stack?",
            "options": ["React", "Vue", "Vanilla JS"],
        },
        lambda q, opts: "use vanilla JS, single file",
    )
    assert "use vanilla JS, single file" in out


def test_ask_user_rejects_bad_options():
    out = resolve_ask_user(
        {"question": "Which stack?", "options": ["only one"]},
        lambda q, opts: "x",
    )
    assert "tool error" in out.lower()
