from langbridge_code.llm.trace import ThoughtEvent, extract_trace_events
from langbridge_code.ui.bridge import format_approval_request


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

    events = extract_trace_events(output, label="Coder", include_message=True)

    assert events == [
        ThoughtEvent(role="Coder", kind="thought", text="Inspect the README."),
        ThoughtEvent(
            role="Coder",
            kind="action",
            text='read_file({"path":"README.md"})',
            tool_name="read_file",
            arguments='{"path":"README.md"}',
        ),
    ]


def test_format_approval_request_includes_role_tool_and_path():
    assert (
        format_approval_request("Coder", "Edit", {"path": "x.py"})
        == "Coder: approve Edit on x.py?"
    )


def test_format_approval_request_summarizes_delegate_task():
    assert (
        format_approval_request(
            "Planner",
            "update_plan",
            {"content": "# Todo\n- [ ] Add monster"},
        )
        == "Planner: approve update_plan?"
    )
