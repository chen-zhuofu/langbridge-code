from langbridge_cli.parse import extract_reasoning_summaries, print_step_trace
from langbridge_cli.prompt import SYSTEM_PROMPT
from langbridge_cli.roles import L3_TEST_ENGINEER_PROMPT, L4_ENGINEER_PROMPT


def test_system_prompt_defines_pm_routing_role():
    assert "the PM for a multi-agent coding team" in SYSTEM_PROMPT
    assert "clarify requirements" in SYSTEM_PROMPT
    assert "Send that task brief to the L4 engineer" in SYSTEM_PROMPT
    assert "When an L5 Ralph loop is available" in SYSTEM_PROMPT
    assert "required purpose argument" in SYSTEM_PROMPT


def test_engineering_guidelines_live_in_specialist_prompts():
    assert "Think before coding." in L4_ENGINEER_PROMPT
    assert "Make surgical changes." in L4_ENGINEER_PROMPT
    assert "Work toward verifiable goals." in L4_ENGINEER_PROMPT
    assert "avoided unrequested features" in L3_TEST_ENGINEER_PROMPT


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
