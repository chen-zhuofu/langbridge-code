from langbridge_cli.llm.trace import ThoughtEvent, extract_trace_events
from langbridge_cli.ui.tui import format_approval_request


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
