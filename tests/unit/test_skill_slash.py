import pytest

from langbridge_code.skills import (
    expand_skill_slash,
    format_skill_slash_turn,
    parse_skill_slash,
    resolve_skill_slash,
    substitute_arguments,
)


def test_parse_skill_slash_basic():
    assert parse_skill_slash("/grilling") == ("grilling", "")
    assert parse_skill_slash("/grilling focus on auth") == ("grilling", "focus on auth")
    assert parse_skill_slash("  /writing-simple-plans  x  ") == ("writing-simple-plans", "x")


def test_parse_skill_slash_ignores_non_slash_and_reserved():
    assert parse_skill_slash("grilling me") is None
    assert parse_skill_slash("/help") is None
    assert parse_skill_slash("/goal ship it") is None
    assert parse_skill_slash("/queue clear") is None


def test_substitute_arguments_placeholders():
    body = "Do $ARGUMENTS with $0 then $ARGUMENTS[1]."
    assert substitute_arguments(body, "alpha beta") == "Do alpha beta with alpha then beta."


def test_substitute_arguments_appends_when_no_placeholder():
    assert substitute_arguments("Just grill.", "this plan") == (
        "Just grill.\n\nARGUMENTS: this plan"
    )
    assert substitute_arguments("Just grill.", "") == "Just grill."


def test_resolve_expanded_known_skill():
    status, content = resolve_skill_slash("/grilling stress-test the API plan")
    assert status == "expanded"
    assert 'The user invoked the /grilling skill via slash command.' in content
    assert '<skill name="grilling">' in content
    assert "Interview me relentlessly" in content
    assert "ARGUMENTS: stress-test the API plan" in content


def test_resolve_unknown_skill():
    assert resolve_skill_slash("/not-a-real-skill") == ("unknown", "not-a-real-skill")


def test_resolve_passthrough():
    assert resolve_skill_slash("please grill this") == ("passthrough", "please grill this")
    assert resolve_skill_slash("/help") == ("passthrough", "/help")


def test_expand_skill_slash_raises_on_unknown():
    with pytest.raises(FileNotFoundError, match="not-a-real-skill"):
        expand_skill_slash("/not-a-real-skill")


def test_format_skill_slash_turn_with_arguments_placeholder():
    body = "Run with $ARGUMENTS."
    out = format_skill_slash_turn("demo", body, "one two")
    assert "Run with one two." in out
    assert "ARGUMENTS:" not in out
