from langbridge_cli.main import SYSTEM_PROMPT, extract_reasoning_summaries, print_step_trace


def test_system_prompt_includes_karpathy_guidelines():
    assert "Think before coding." in SYSTEM_PROMPT
    assert "Simplicity first." in SYSTEM_PROMPT
    assert "Make surgical changes." in SYSTEM_PROMPT
    assert "Work toward verifiable goals." in SYSTEM_PROMPT
    assert "Before every tool call" in SYSTEM_PROMPT


def test_extract_reasoning_summaries():
    output = [
        {
            "type": "reasoning",
            "summary": [
                {"type": "summary_text", "text": "Inspect the repository."},
            ],
        },
        {"type": "message", "content": []},
    ]

    assert extract_reasoning_summaries(output) == ["Inspect the repository."]


def test_print_step_trace_uses_message_rationale(capsys):
    output = [
        {
            "type": "reasoning",
            "summary": [
                {"type": "summary_text", "text": "Reasoning fallback."},
            ],
        },
        {
            "type": "message",
            "content": [
                {"type": "output_text", "text": "Read the target file."},
            ],
        },
        {
            "type": "function_call",
            "name": "read_file",
            "arguments": '{"path":"README.md"}',
        },
    ]

    print_step_trace(output, include_message=True)

    assert capsys.readouterr().out == (
        "\nThought: Read the target file.\n"
        'Action: read_file({"path":"README.md"})\n'
    )


def test_print_step_trace_falls_back_to_reasoning_summary(capsys):
    output = [
        {
            "type": "reasoning",
            "summary": [
                {"type": "summary_text", "text": "Inspect the repository."},
            ],
        }
    ]

    print_step_trace(output)

    assert capsys.readouterr().out == "\nThought: Inspect the repository.\n"
