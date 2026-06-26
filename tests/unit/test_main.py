from langbridge_cli.agents.agent import pm_should_continue
from langbridge_cli.llm.parse import DIM, RESET, extract_reasoning_summaries, print_step_trace
from langbridge_cli.agents.roles import L3_TEST_ENGINEER_PROMPT, L4_ENGINEER_PROMPT, SYSTEM_PROMPT


def test_pm_loop_continues_only_while_bug_open():
    assert pm_should_continue("subtasks remain\nBUG_STATUS: OPEN")
    assert pm_should_continue("e2e verify found a bug\nBUG_STATUS: OPEN")
    assert not pm_should_continue("all done and e2e verify passed\nBUG_STATUS: NONE")
    assert not pm_should_continue("answered a question, no plan needed\nBUG_STATUS: NONE")


def test_system_prompt_defines_pm_loop_role():
    assert "the PM for a multi-agent coding team" in SYSTEM_PROMPT
    assert "You run as an agentic outer loop" in SYSTEM_PROMPT
    assert "Always check the todo_list first" in SYSTEM_PROMPT
    assert "update_plan" in SYSTEM_PROMPT
    assert "The last subtask in the todo_list must always be an end-to-end (e2e) test" in SYSTEM_PROMPT
    assert "do a final hand-debug pass" in SYSTEM_PROMPT
    assert "run the e2e test once more to verify" in SYSTEM_PROMPT
    assert "BUG_STATUS: OPEN" in SYSTEM_PROMPT
    assert "BUG_STATUS: NONE" in SYSTEM_PROMPT
    assert "RALPH_STATUS" not in SYSTEM_PROMPT
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
        f"\n{DIM}Agent: Read the target file.{RESET}\n"
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

    print_step_trace(output, include_message=True, label="L3 test engineer")

    assert capsys.readouterr().out == (
        f"\n{DIM}L3 test engineer: Inspect the README before editing.{RESET}\n"
        f'{DIM}L3 test engineer: ↳ read_file({{"path":"README.md"}}){RESET}\n'
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
