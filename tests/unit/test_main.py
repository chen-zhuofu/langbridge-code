from langbridge_code.agents.agent import pm_should_continue
from langbridge_code.llm.parse import DIM, RESET, extract_reasoning_summaries, print_step_trace
from langbridge_code.agents.roles import L3_TEST_ENGINEER_PROMPT, L4_ENGINEER_PROMPT, SYSTEM_PROMPT


def test_workflow_runs_to_completion_in_one_call():
    assert not pm_should_continue("subtasks remain\nBUG_STATUS: OPEN")
    assert not pm_should_continue("all done\nBUG_STATUS: NONE")


def test_system_prompt_is_langbridge_code_chat():
    assert "LangBridge Code" in SYSTEM_PROMPT
    assert "Do not reveal" in SYSTEM_PROMPT
    assert "BUG_STATUS" not in SYSTEM_PROMPT
    assert "update_plan" not in SYSTEM_PROMPT


def test_engineering_guidelines_live_in_specialist_prompts():
    assert "CODER_STATUS: READY_FOR_REVIEW" in L4_ENGINEER_PROMPT
    assert "REVIEW_VERDICT: PASS" in L3_TEST_ENGINEER_PROMPT


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
        f"\n{DIM}Agent: Read the target file.{RESET}\n"
        f'{DIM}Agent: ↳ read_file({{"path":"README.md"}}){RESET}\n'
    )


def test_print_step_trace_surfaces_reasoning_with_tool_calls(capsys):
    output = [
        {
            "type": "reasoning",
            "summary": [
                {"type": "summary_text", "text": "I should read the file first."},
            ],
        },
        {
            "type": "function_call",
            "name": "read_file",
            "arguments": '{"purpose":"Inspect the README.","path":"README.md"}',
        },
    ]

    print_step_trace(output, include_message=True)

    assert capsys.readouterr().out == (
        f"\n{DIM}Agent: I should read the file first.{RESET}\n"
        f"\n{DIM}Agent: Inspect the README.{RESET}\n"
        f'{DIM}Agent: ↳ read_file({{"path":"README.md"}}){RESET}\n'
    )


def test_print_step_trace_uses_tool_purpose(capsys):
    output = [
        {
            "type": "message",
            "content": [
                {"type": "output_text", "text": "This message should not win over purpose."},
            ],
        },
        {
            "type": "function_call",
            "name": "read_file",
            "arguments": '{"purpose":"Inspect the README before editing.","path":"README.md"}',
        },
    ]

    print_step_trace(output, include_message=True, label="Reviewer")

    assert capsys.readouterr().out == (
        f"\n{DIM}Reviewer: Inspect the README before editing.{RESET}\n"
        f'{DIM}Reviewer: ↳ read_file({{"path":"README.md"}}){RESET}\n'
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

    assert capsys.readouterr().out == f"\n{DIM}Agent: Inspect the repository.{RESET}\n"
