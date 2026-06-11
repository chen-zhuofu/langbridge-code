from langbridge_cli.agent import add_hidden_tool_context, append_pm_l3_review
from langbridge_cli.multi_agent import L4_TOOL_SCHEMAS, max_steps_report, run_specialist_agent, run_specialist_tool_call
from langbridge_cli.tools import MAIN_TOOL_SCHEMAS, MAIN_TOOLS, TOOL_SCHEMAS, TOOLS
from langbridge_cli.multi_agent import l3_review_passed
from langbridge_cli.tools.agents import ask_l3_test_engineer, ask_l4_engineer


def test_l3_test_engineer_tool_is_registered():
    assert "ask_l3_test_engineer" in TOOLS
    assert any(schema["name"] == "ask_l3_test_engineer" for schema in TOOL_SCHEMAS)
    assert "ask_l4_engineer" in TOOLS
    assert any(schema["name"] == "ask_l4_engineer" for schema in TOOL_SCHEMAS)
    assert any(schema["name"] == "delete_file" for schema in L4_TOOL_SCHEMAS)
    assert set(MAIN_TOOLS) == {"list_dir", "find_files", "read_file", "search_files", "ask_l4_engineer"}
    assert [schema["name"] for schema in MAIN_TOOL_SCHEMAS] == [
        "list_dir",
        "find_files",
        "read_file",
        "search_files",
        "ask_l4_engineer",
    ]


def test_hidden_tool_context_is_passed_only_when_supported():
    def specialist_tool(task, api_key=None, model=None):
        return task, api_key, model

    arguments = add_hidden_tool_context(specialist_tool, {"task": "verify tests"}, "key", "model")

    assert arguments == {"task": "verify tests", "api_key": "key", "model": "model"}


def test_l3_tool_uses_runner(monkeypatch):
    calls = []

    def fake_runner(api_key, model, task, context):
        calls.append((api_key, model, task, context))
        return "L3 verdict"

    monkeypatch.setattr("langbridge_cli.tools.agents.run_l3_test_engineer", fake_runner)

    result = ask_l3_test_engineer("verify tests", "changed tests/test_x.py", api_key="key", model="model")

    assert result == "L3 verdict"
    assert calls == [("key", "model", "verify tests", "changed tests/test_x.py")]


def test_l4_tool_uses_runner(monkeypatch):
    calls = []

    def fake_runner(api_key, model, task, context, feedback):
        calls.append((api_key, model, task, context, feedback))
        return "L4 report"

    monkeypatch.setattr("langbridge_cli.tools.agents.run_l4_engineer", fake_runner)

    result = ask_l4_engineer("implement feature", "context", "feedback", api_key="key", model="model")

    assert result == "L4 report"
    assert calls == [("key", "model", "implement feature", "context", "feedback")]


def test_l3_review_passed_requires_pass_verdict():
    assert l3_review_passed("REVIEW_VERDICT: PASS\nEvidence: tests passed")
    assert not l3_review_passed("REVIEW_VERDICT: NEEDS_WORK\nIssues: missing coverage")
    assert not l3_review_passed("REVIEW_VERDICT: FAIL\nIssues: tests failed")


def test_l4_write_tool_requires_approval(monkeypatch):
    monkeypatch.setattr("langbridge_cli.multi_agent.approve_l4_write_tool", lambda name, arguments: False)

    result = run_specialist_tool_call(
        {
            "type": "function_call",
            "name": "create_file",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"create_file": lambda **arguments: "created"},
        "L4 engineer",
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Tool error: create_file was not approved",
    }


def test_l4_write_tool_runs_after_approval(monkeypatch):
    monkeypatch.setattr("langbridge_cli.multi_agent.approve_l4_write_tool", lambda name, arguments: True)

    result = run_specialist_tool_call(
        {
            "type": "function_call",
            "name": "create_file",
            "call_id": "call_1",
            "arguments": '{"path":"x.py","content":"print(1)"}',
        },
        {"create_file": lambda **arguments: f"created {arguments['path']}"},
        "L4 engineer",
    )

    assert result == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "created x.py",
    }


def test_pm_appends_l3_review_when_l4_ready(monkeypatch):
    calls = []

    def fake_l3(api_key, model, task, context):
        calls.append((api_key, model, task, context))
        return "REVIEW_VERDICT: PASS\nEvidence: tests pass"

    monkeypatch.setattr("langbridge_cli.multi_agent.run_l3_test_engineer", fake_l3)

    output = append_pm_l3_review(
        "key",
        "model",
        {"task": "implement calculator", "context": "repo context"},
        "L4_STATUS: READY_FOR_REVIEW\nSummary: calculator implemented",
    )

    assert "PM_DETERMINISTIC_L3_REVIEW:" in output
    assert "REVIEW_VERDICT: PASS" in output
    assert "PM_REVIEW_STATUS: OK" in output
    assert calls[0][:3] == ("key", "model", "implement calculator")
    assert "repo context" in calls[0][3]
    assert "L4 completed work and is ready for PM-triggered L3 review." in calls[0][3]
    assert "L4_STATUS: READY_FOR_REVIEW" in calls[0][3]


def test_pm_does_not_review_l4_when_not_ready(monkeypatch):
    def fake_l3(api_key, model, task, context):
        raise AssertionError("L3 should not run unless L4 is ready for review")

    monkeypatch.setattr("langbridge_cli.multi_agent.run_l3_test_engineer", fake_l3)

    output = append_pm_l3_review(
        "key",
        "model",
        {"task": "implement calculator"},
        "L4_STATUS: IN_PROGRESS\nSummary: still writing tests",
    )

    assert output.startswith("L4_STATUS: IN_PROGRESS")
    assert "PM_DETERMINISTIC_L3_REVIEW" not in output


def test_l4_max_steps_report_includes_recent_tool_activity():
    report = max_steps_report(
        "L4 engineer",
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

    assert report.startswith("L4_STATUS: IN_PROGRESS")
    assert "maximum specialist tool-call steps" in report
    assert 'delete_file({"path":"synthetic-env/calculator.py"})' in report
    assert "Deleted synthetic-env/calculator.py." in report


def test_specialist_max_steps_fallback_reports_tool_history(monkeypatch):
    calls = []

    def fake_response(api_key, model, messages, tool_schemas, label):
        calls.append(messages)
        return {
            "output": [
                {
                    "type": "function_call",
                    "name": "delete_file",
                    "call_id": f"call_{len(calls)}",
                    "arguments": '{"path":"synthetic-env/calculator.py"}',
                }
            ]
        }

    monkeypatch.setattr("langbridge_cli.multi_agent.MAX_SPECIALIST_AGENT_STEPS", 1)
    monkeypatch.setattr("langbridge_cli.multi_agent.create_specialist_response", fake_response)
    monkeypatch.setattr("langbridge_cli.multi_agent.approve_l4_write_tool", lambda name, arguments: True)

    report = run_specialist_agent(
        "key",
        "model",
        "system",
        "user",
        [{"name": "delete_file"}],
        {"delete_file": lambda path: f"Deleted {path}."},
        "L4 engineer",
    )

    assert report.startswith("L4_STATUS: IN_PROGRESS")
    assert "delete_file" in report
    assert "Deleted synthetic-env/calculator.py." in report
