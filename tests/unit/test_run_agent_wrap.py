"""Tests for eval run_agent task wrapping."""
from run_agent import provider_failure_from_report, wrap_issue_as_task


def test_wrap_issue_as_task_preserves_public_benchmark_text():
    issue = (
        "Variables no longer loaded.\n\n"
        "### Questions\n\n"
        "* Is this a bug or a new design direction?\n"
    )
    task = wrap_issue_as_task(issue)
    assert task == issue.strip()


def test_wrap_issue_as_task_empty():
    assert wrap_issue_as_task("") == ""
    assert wrap_issue_as_task("   ") == ""


def test_provider_failure_from_report_detects_formatted_api_error():
    report = (
        "Request failed: Error code: 402 - "
        "{'error': {'message': 'Insufficient Balance'}}"
    )
    assert provider_failure_from_report(report) == report
    assert (
        provider_failure_from_report(
            "API daily token quota is exhausted (provider TPD limit)."
        )
        == "API daily token quota is exhausted (provider TPD limit)."
    )


def test_provider_failure_from_report_ignores_normal_agent_text():
    assert provider_failure_from_report("Fixed HTTP 402 handling.") == ""
    assert (
        provider_failure_from_report(
            "The test fixture contains 'Request failed: Error code: 402'."
        )
        == ""
    )
