from langbridge_code.tools.agent_worker_reviewer import run_worker_component
from langbridge_code.tools.agent_worker_reviewer import (
    CODE_WORKER_TOOL_SCHEMAS,
    approve_worker_write_tool,
    build_code_worker_toolkit,
    max_steps_report,
    run_worker_tool_call,
)
from langbridge_code.tools.agent_worker_reviewer import build_reviewer_toolkit, reviewer_review_passed
from langbridge_code.agents.system_prompt import (
    WORKER_ENGINEER_PROMPT,
    REVIEWER_ENGINEER_PROMPT,
)
from langbridge_code.tools import MAIN_TOOL_SCHEMAS, MAIN_TOOLS, TOOLS


def test_engineering_guidelines_live_in_specialist_prompts():
    assert "Think before coding." not in WORKER_ENGINEER_PROMPT
    assert "WORKER_STATUS: READY_FOR_REVIEW" in WORKER_ENGINEER_PROMPT
    assert "REVIEW_VERDICT: PASS" in REVIEWER_ENGINEER_PROMPT


def test_main_tools_exclude_legacy_specialists():
    assert "ask_l4_engineer" not in TOOLS
    assert "ask_l5_engineer" not in TOOLS
    assert set(MAIN_TOOLS) >= {
        "list_dir",
        "glob",
        "read_file",
        "read_many",
        "grep",
        "edit_file",
        "write",
        "multi_edit",
        "apply_patch",
        "delete_file",
        "run_tests",
        "bash",
        "powershell",
        "git_status",
        "git_diff",
        "git_commit",
        "lsp",
        "read_webpage",
        "browse_webpage",
        "read_plan",
        "clear_plan",
        "update_plan",
        "read_skill",
    }
    main_names = {schema["name"] for schema in MAIN_TOOL_SCHEMAS}
    assert main_names >= {
        "list_dir",
        "glob",
        "read_file",
        "read_many",
        "grep",
        "edit_file",
        "write",
        "multi_edit",
        "apply_patch",
        "delete_file",
        "run_tests",
        "bash",
        "powershell",
        "git_status",
        "git_diff",
        "git_commit",
        "lsp",
        "read_plan",
        "clear_plan",
        "update_plan",
        "read_webpage",
        "browse_webpage",
        "read_skill",
    }
    assert any(schema["name"] == "delete_file" for schema in CODE_WORKER_TOOL_SCHEMAS)
    assert any(schema["name"] == "read_plan" for schema in CODE_WORKER_TOOL_SCHEMAS)
    assert not any(schema["name"] == "check_subtask" for schema in MAIN_TOOL_SCHEMAS)
    assert not any(schema["name"] == "check_subtask" for schema in CODE_WORKER_TOOL_SCHEMAS)
    coder_tools, coder_schemas = build_code_worker_toolkit(api_key="k", model="m")
    assert "agent_explorer" not in coder_tools
    assert not any(schema["name"] == "agent_explorer" for schema in coder_schemas)
    reviewer_tools, reviewer_schemas = build_reviewer_toolkit(api_key="k", model="m")
    assert "agent_explorer" not in reviewer_tools
    assert not any(schema["name"] == "agent_explorer" for schema in reviewer_schemas)
    assert "update_plan" in MAIN_TOOLS
    assert any(schema["name"] == "update_plan" for schema in MAIN_TOOL_SCHEMAS)
    for schema in MAIN_TOOL_SCHEMAS + coder_schemas:
        assert "purpose" in schema["parameters"]["properties"]
        assert "purpose" in schema["parameters"]["required"]


def test_reviewer_passed_requires_pass_verdict():
    assert reviewer_review_passed("REVIEW_VERDICT: PASS\nEvidence: tests passed")
    assert not reviewer_review_passed("REVIEW_VERDICT: NEEDS_WORK\nIssues: missing coverage")
    assert not reviewer_review_passed("REVIEW_VERDICT: FAIL\nIssues: tests failed")


def test_coder_write_tool_requires_approval(monkeypatch):
    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.approve_worker_write_tool", lambda name, arguments: False)

    result = run_worker_tool_call(
        {
            "type": "function_call",
            "name": "write",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"write": lambda **arguments: "created"},
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Tool error: write was not approved",
    }


def test_coder_write_tool_runs_after_approval(monkeypatch):
    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.approve_worker_write_tool", lambda name, arguments: True)

    result = run_worker_tool_call(
        {
            "type": "function_call",
            "name": "write",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"write": lambda **arguments: f"created {arguments['path']}"},
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "created x.py",
    }


def test_specialist_tool_strips_purpose_before_execution(monkeypatch):
    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.approve_worker_write_tool", lambda name, arguments: True)

    result = run_worker_tool_call(
        {
            "type": "function_call",
            "name": "write",
            "call_id": "call_1",
            "arguments": '{"purpose":"Create the target file.","path":"x.py","content":"print(1)"}',
        },
        {"write": lambda **arguments: sorted(arguments)},
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": ["content", "path"],
    }


def test_coder_write_tool_uses_approval_callback():
    approvals = []

    result = run_worker_tool_call(
        {
            "type": "function_call",
            "name": "write",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"write": lambda **arguments: f"created {arguments['path']}"},
        approval_callback=lambda role, name, arguments: approvals.append((role, name, arguments)) or True,
    )

    assert approvals == [("Worker", "write", {"path": "x.py", "content": "print(1)"})]
    assert result["output"] == "created x.py"


def test_run_worker_component_delegates_to_coder_reviewer(monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.tools.agent_worker_reviewer.run_worker_reviewer_loop",
        lambda *args, **kwargs: (True, "WORKER_STATUS: READY_FOR_REVIEW\nSummary: done"),
    )

    output = run_worker_component("key", "model", {"task": "implement calculator", "context": "repo context"})

    assert "WORKFLOW_REVIEW_STATUS: OK" in output
    assert "READY_FOR_REVIEW" in output


def test_coder_max_steps_report_includes_recent_tool_activity():
    report = max_steps_report(
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

    assert report.startswith("WORKER_STATUS: IN_PROGRESS")
    assert "maximum specialist tool-call steps" in report
    assert 'delete_file({"path":"synthetic-env/calculator.py"})' in report


def test_specialist_max_steps_fallback_reports_tool_history(monkeypatch):
    def fake_response(api_key, model, messages, tool_schemas, label, **kwargs):
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

    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.MAX_WORKER_STEPS", 1)
    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.create_model_response", fake_response)
    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.approve_worker_write_tool", lambda name, arguments: True)

    from langbridge_code.tools.agent_worker_reviewer import WorkerSession

    session = WorkerSession("key", "model", [{"name": "delete_file"}], {"delete_file": lambda path: f"Deleted {path}."})
    report = session.send("user")

    assert report.startswith("WORKER_STATUS: IN_PROGRESS")
    assert "delete_file" in report
