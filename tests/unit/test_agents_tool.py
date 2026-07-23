from langbridge_code.tools.agent_worker_reviewer import run_worker_component
from langbridge_code.tools.agent_worker_reviewer import (
    AGENT_WORKER_TOOL_SCHEMA,
    CODE_WORKER_TOOL_SCHEMAS,
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


def test_worker_dispatch_requires_verbatim_task_contract():
    parameters = AGENT_WORKER_TOOL_SCHEMA["parameters"]
    properties = parameters["properties"]

    assert "task_contract" in parameters["required"]
    assert "supplemental_context" in properties
    assert "prompt" not in properties
    assert "word-for-word" in properties["task_contract"]["description"].lower()
    assert "WORKER_STATUS: BLOCKED" in WORKER_ENGINEER_PROMPT
    assert "Acceptance checklist" in REVIEWER_ENGINEER_PROMPT


def test_main_tools_exclude_legacy_specialists():
    assert "ask_l4_engineer" not in TOOLS
    assert "ask_l5_engineer" not in TOOLS
    assert set(MAIN_TOOLS) >= {
        "glob",
        "read_file",
        "grep",
        "Edit",
        "write",
        "bash",
        "powershell",
        "read_webpage",
        "read_skill",
    }
    assert "run_tests" not in MAIN_TOOLS
    main_names = {schema["name"] for schema in MAIN_TOOL_SCHEMAS}
    assert main_names >= {
        "glob",
        "read_file",
        "grep",
        "Edit",
        "write",
        "bash",
        "powershell",
        "read_webpage",
        "read_skill",
    }
    assert "run_tests" not in main_names
    assert any(schema["name"] == "write" for schema in CODE_WORKER_TOOL_SCHEMAS)
    # Workers never read the plan file; the main agent owns todo_list.md.
    assert not any(schema["name"] == "read_plan" for schema in CODE_WORKER_TOOL_SCHEMAS)
    assert not any(schema["name"] == "check_subtask" for schema in MAIN_TOOL_SCHEMAS)
    assert not any(schema["name"] == "check_subtask" for schema in CODE_WORKER_TOOL_SCHEMAS)
    coder_tools, coder_schemas = build_code_worker_toolkit(api_key="k", model="m")
    assert "agent_explorer" not in coder_tools
    assert not any(schema["name"] == "agent_explorer" for schema in coder_schemas)
    assert any(schema["name"] == "memory_writer" for schema in coder_schemas)
    reviewer_tools, reviewer_schemas = build_reviewer_toolkit(api_key="k", model="m")
    assert "agent_explorer" not in reviewer_tools
    assert not any(schema["name"] == "agent_explorer" for schema in reviewer_schemas)
    assert any(schema["name"] == "memory_writer" for schema in reviewer_schemas)
    assert "bash" in reviewer_tools
    assert "run_tests" not in reviewer_tools
    assert "run_tests" not in coder_tools
    # Plan tools are gone; the plan is a plain file edited with file tools.
    assert "update_plan" not in MAIN_TOOLS
    assert "read_plan" not in MAIN_TOOLS
    assert "clear_plan" not in MAIN_TOOLS
    for schema in MAIN_TOOL_SCHEMAS + coder_schemas:
        assert "purpose" in schema["parameters"]["properties"]
        assert "purpose" in schema["parameters"]["required"]


def test_reviewer_passed_requires_pass_verdict():
    assert reviewer_review_passed("REVIEW_VERDICT: PASS\nEvidence: tests passed")
    assert not reviewer_review_passed("REVIEW_VERDICT: NEEDS_WORK\nIssues: missing coverage")
    assert not reviewer_review_passed("REVIEW_VERDICT: FAIL\nIssues: tests failed")


def test_reviewer_passed_tolerates_markdown_emphasis():
    assert reviewer_review_passed("**REVIEW_VERDICT: PASS**\n\nEvidence: tests passed")
    assert reviewer_review_passed("`REVIEW_VERDICT: PASS`\nEvidence: ok")
    assert not reviewer_review_passed("**REVIEW_VERDICT: NEEDS_WORK**\nIssues: bug")


def test_reviewer_passed_tolerates_prose_preamble():
    """Regression: a PASS after a prose preamble ping-ponged the loop forever."""
    report = (
        "Same submission — fourth round, same file, same diff. Already approved.\n"
        "REVIEW_VERDICT: PASS\n"
        "**Evidence:** File unchanged, spot-read confirmed.\n"
        "**Issues:** None."
    )
    assert reviewer_review_passed(report)
    assert not reviewer_review_passed(
        "Looks close but one test fails.\nREVIEW_VERDICT: NEEDS_WORK\nFix test_x first."
    )
    # A quoted marker mid-sentence is not a verdict line.
    assert not reviewer_review_passed("Reply must end with REVIEW_VERDICT: PASS when done.")


def test_worker_ready_tolerates_prose_preamble():
    from langbridge_code.tools.agent_worker_reviewer import worker_blocked, worker_ready_for_review

    assert worker_ready_for_review("WORKER_STATUS: READY_FOR_REVIEW\nSummary: done")
    assert worker_ready_for_review(
        "## Summary\nTask complete, verified.\nWORKER_STATUS: READY_FOR_REVIEW"
    )
    assert not worker_ready_for_review("WORKER_STATUS: IN_PROGRESS\nSummary: blocked on X")
    assert worker_blocked("Conflicting requirements.\nWORKER_STATUS: BLOCKED")
    assert not worker_blocked("WORKER_STATUS: IN_PROGRESS\nSummary: still working")


def test_worker_write_tool_runs_without_approval():
    """Routine writes are auto-approved; no approval callback needed."""
    result = run_worker_tool_call(
        {
            "type": "function_call",
            "name": "write",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"write": lambda **arguments: f"created {arguments['path']}"},
        approval_callback=lambda role, name, arguments: (_ for _ in ()).throw(
            AssertionError("routine write must not ask for approval")
        ),
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "created x.py",
    }


def test_worker_high_risk_bash_requires_approval():
    result = run_worker_tool_call(
        {
            "type": "function_call",
            "name": "bash",
            "call_id": "call_1",
            "arguments": '{"command":"rm -rf build/"}',
        },
        {"bash": lambda **arguments: "should not run"},
        approval_callback=lambda role, name, arguments: False,
    )

    assert result["output"].startswith("Tool error: bash was not approved")


def test_worker_high_risk_bash_runs_after_approval():
    approvals = []

    result = run_worker_tool_call(
        {
            "type": "function_call",
            "name": "bash",
            "call_id": "call_1",
            "arguments": '{"command":"sudo apt install jq"}',
        },
        {"bash": lambda **arguments: "installed"},
        approval_callback=lambda role, name, arguments: approvals.append((role, name)) or True,
    )

    assert approvals == [("Worker", "bash")]
    assert result["output"] == "installed"


def test_worker_ordinary_bash_runs_without_approval():
    result = run_worker_tool_call(
        {
            "type": "function_call",
            "name": "bash",
            "call_id": "call_1",
            "arguments": '{"command":"pytest -q && git commit -m test"}',
        },
        {"bash": lambda **arguments: "ok"},
        approval_callback=lambda role, name, arguments: (_ for _ in ()).throw(
            AssertionError("ordinary command must not ask for approval")
        ),
    )

    assert result["output"] == "ok"


def test_specialist_tool_strips_purpose_before_execution():
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
                    "name": "Edit",
                    "arguments": '{"path":"synthetic-env/calculator.py","old_string":"a","new_string":"b"}',
                },
                "output": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "Edited synthetic-env/calculator.py: replaced 1 occurrence.",
                },
            }
        ],
    )

    assert report.startswith("WORKER_STATUS: IN_PROGRESS")
    assert "maximum specialist tool-call steps" in report
    assert 'Edit({"path":"synthetic-env/calculator.py"' in report


def test_specialist_max_steps_fallback_reports_tool_history(monkeypatch):
    def fake_response(api_key, model, messages, tool_schemas, label, **kwargs):
        return {
            "output": [
                {
                    "type": "function_call",
                    "name": "Edit",
                    "call_id": "call_1",
                    "arguments": '{"path":"synthetic-env/calculator.py","old_string":"a","new_string":"b"}',
                }
            ]
        }

    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.MAX_WORKER_STEPS", 1)
    monkeypatch.setattr("langbridge_code.tools.agent_worker_reviewer.create_model_response", fake_response)

    from langbridge_code.tools.agent_worker_reviewer import WorkerSession

    session = WorkerSession(
        "key",
        "model",
        [{"name": "Edit"}],
        {"Edit": lambda path, old_string, new_string: f"Edited {path}."},
    )
    report = session.send("user")

    assert report.startswith("WORKER_STATUS: IN_PROGRESS")
    assert "Edit" in report
