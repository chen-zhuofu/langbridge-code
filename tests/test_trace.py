from langbridge_cli.trace import ThoughtEvent, extract_trace_events
from langbridge_cli.ui import (
    format_approval_request,
    format_current_thought,
    format_trace_event,
)


def test_extract_trace_events_prefers_tool_purpose_and_hides_it_from_action():
    output = [
        {
            "type": "message",
            "content": [{"type": "output_text", "text": "Fallback message."}],
        },
        {
            "type": "function_call",
            "name": "read_file",
            "arguments": '{"purpose":"Inspect the README.","path":"README.md"}',
        },
    ]

    events = extract_trace_events(output, label="PM agent", include_message=True)

    assert events == [
        ThoughtEvent(role="PM agent", kind="thought", text="Inspect the README."),
        ThoughtEvent(
            role="PM agent",
            kind="action",
            text='read_file({"path":"README.md"})',
            tool_name="read_file",
            arguments='{"path":"README.md"}',
        ),
    ]


def test_format_trace_event_marks_actions():
    event = ThoughtEvent(
        role="L4 engineer",
        kind="action",
        text='edit_file({"path":"x.py"})',
    )

    assert format_trace_event(event) == 'L4 engineer: ↳ edit_file({"path":"x.py"})'


def test_format_current_thought_ignores_actions():
    thought = ThoughtEvent(role="PM agent", kind="thought", text="Inspect the repository.")
    action = ThoughtEvent(
        role="PM agent",
        kind="action",
        text='list_dir({"path":"."})',
    )

    assert format_current_thought(thought) == "PM agent: Inspect the repository."
    assert format_current_thought(action) == ""


def test_format_approval_request_includes_role_tool_and_path():
    assert (
        format_approval_request("L4 engineer", "edit_file", {"path": "x.py"})
        == "L4 engineer: approve edit_file on x.py?"
    )


def test_format_approval_request_summarizes_l4_delegate_task():
    assert (
        format_approval_request(
            "PM agent",
            "ask_l4_engineer",
            {"task": "Add a third monster to the default game."},
        )
        == "PM agent: approve ask_l4_engineer? Task: Add a third monster to the default game."
    )
