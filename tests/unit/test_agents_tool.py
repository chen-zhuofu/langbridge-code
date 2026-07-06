from langbridge_code.agents.agent import pm_should_continue, run_l4_component
from langbridge_code.agents.multi_agent import (
    L4_TOOL_SCHEMAS,
    max_steps_report,
    reviewer_review_passed,
    run_specialist_agent,
    run_specialist_tool_call,
)
from langbridge_code.agents.roles import (
    CHAT_SYSTEM_PROMPT,
    CODER_ENGINEER_PROMPT,
    L3_TEST_ENGINEER_PROMPT,
    L4_ENGINEER_PROMPT,
    SYSTEM_PROMPT,
)
from langbridge_code.tools import MAIN_TOOL_SCHEMAS, MAIN_TOOLS, TOOLS


def test_workflow_does_not_continue_after_one_turn():
    assert not pm_should_continue("subtasks remain\nBUG_STATUS: OPEN")
    assert not pm_should_continue("all done\nBUG_STATUS: NONE")


def test_chat_system_prompt():
    assert SYSTEM_PROMPT == CHAT_SYSTEM_PROMPT
    assert "LangBridge Code" in SYSTEM_PROMPT
    assert "Do not reveal" in SYSTEM_PROMPT
    assert "BUG_STATUS" not in SYSTEM_PROMPT


def test_engineering_guidelines_live_in_specialist_prompts():
    assert "Think before coding." not in CODER_ENGINEER_PROMPT  # slim workflow prompt
    assert "CODER_STATUS: READY_FOR_REVIEW" in CODER_ENGINEER_PROMPT
    assert "REVIEW_VERDICT: PASS" in L3_TEST_ENGINEER_PROMPT
    assert L4_ENGINEER_PROMPT == CODER_ENGINEER_PROMPT


def test_main_tools_exclude_legacy_pm_specialists():
    assert "ask_l4_engineer" not in TOOLS
    assert "ask_l5_engineer" not in TOOLS
    assert set(MAIN_TOOLS) == {
        "list_dir",
        "glob",
        "read_file",
        "grep",
        "bash",
        "read_webpage",
        "update_plan",
    }
    assert [schema["name"] for schema in MAIN_TOOL_SCHEMAS] == [
        "list_dir",
        "glob",
        "read_file",
        "grep",
        "bash",
        "read_webpage",
        "update_plan",
    ]
    assert any(schema["name"] == "delete_file" for schema in L4_TOOL_SCHEMAS)
    for schema in MAIN_TOOL_SCHEMAS + L4_TOOL_SCHEMAS:
        assert "purpose" in schema["parameters"]["properties"]
        assert "purpose" in schema["parameters"]["required"]


def test_reviewer_passed_requires_pass_verdict():
    assert reviewer_review_passed("REVIEW_VERDICT: PASS\nEvidence: tests passed")
    assert not reviewer_review_passed("REVIEW_VERDICT: NEEDS_WORK\nIssues: missing coverage")
    assert not reviewer_review_passed("REVIEW_VERDICT: FAIL\nIssues: tests failed")


def test_coder_write_tool_requires_approval(monkeypatch):
    monkeypatch.setattr("langbridge_code.agents.multi_agent.approve_l4_write_tool", lambda name, arguments: False)

    result = run_specialist_tool_call(
        {
            "type": "function_call",
            "name": "create_file",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"create_file": lambda **arguments: "created"},
        "Coder",
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Tool error: create_file was not approved",
    }


def test_coder_write_tool_runs_after_approval(monkeypatch):
    monkeypatch.setattr("langbridge_code.agents.multi_agent.approve_l4_write_tool", lambda name, arguments: True)

    result = run_specialist_tool_call(
        {
            "type": "function_call",
            "name": "create_file",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"create_file": lambda **arguments: f"created {arguments['path']}"},
        "Coder",
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "created x.py",
    }


def test_specialist_tool_strips_purpose_before_execution(monkeypatch):
    monkeypatch.setattr("langbridge_code.agents.multi_agent.approve_l4_write_tool", lambda name, arguments: True)

    result = run_specialist_tool_call(
        {
            "type": "function_call",
            "name": "create_file",
            "call_id": "call_1",
            "arguments": '{"purpose":"Create the target file.","path":"x.py","content":"print(1)"}',
        },
        {"create_file": lambda **arguments: sorted(arguments)},
        "Coder",
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": ["content", "path"],
    }


def test_coder_write_tool_uses_approval_callback():
    approvals = []

    result = run_specialist_tool_call(
        {
            "type": "function_call",
            "name": "create_file",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"create_file": lambda **arguments: f"created {arguments['path']}"},
        "Coder",
        approval_callback=lambda role, name, arguments: approvals.append((role, name, arguments)) or True,
    )

    assert approvals == [("Coder", "create_file", {"path": "x.py", "content": "print(1)"})]
    assert result["output"] == "created x.py"


def test_run_l4_component_delegates_to_coder_reviewer(monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.agents.agent.run_coder_reviewer_loop",
        lambda *args, **kwargs: (True, "CODER_STATUS: READY_FOR_REVIEW\nSummary: done"),
    )

    output = run_l4_component("key", "model", {"task": "implement calculator", "context": "repo context"})

    assert "WORKFLOW_REVIEW_STATUS: OK" in output
    assert "READY_FOR_REVIEW" in output


def test_coder_max_steps_report_includes_recent_tool_activity():
    report = max_steps_report(
        "Coder",
        [
            {
                "call": {
                    "name": "delete_file",
                    "arguments": '{"path":"synthetic-env/calculator.py"}',
                },
                "output": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "Deleted synthetic-env/calculator.py.",
                },
            }
        ],
    )

    assert report.startswith("CODER_STATUS: IN_PROGRESS")
    assert "maximum specialist tool-call steps" in report
    assert 'delete_file({"path":"synthetic-env/calculator.py"})' in report


def test_specialist_max_steps_fallback_reports_tool_history(monkeypatch):
    def fake_response(api_key, model, messages, tool_schemas, label):
        return {
            "output": [
                {
                    "type": "function_call",
                    "name": "delete_file",
                    "call_id": "call_1",
                    "arguments": '{"path":"synthetic-env/calculator.py"}',
                }
            ]
        }

    monkeypatch.setattr("langbridge_code.agents.multi_agent.MAX_SPECIALIST_AGENT_STEPS", 1)
    monkeypatch.setattr("langbridge_code.agents.multi_agent.create_specialist_response", fake_response)
    monkeypatch.setattr("langbridge_code.agents.multi_agent.approve_l4_write_tool", lambda name, arguments: True)

    report = run_specialist_agent(
        "key",
        "model",
        "system",
        "user",
        [{"name": "delete_file"}],
        {"delete_file": lambda path: f"Deleted {path}."},
        "Coder",
    )

    assert report.startswith("CODER_STATUS: IN_PROGRESS")
    assert "delete_file" in report
