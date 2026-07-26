"""User-task wrapper for headless eval agent runs."""

TASK_WRAPPER = """\
You are working in a checked-out code repository (current working directory).
Implement the minimal code changes needed to resolve the issue below.
Do not only explain or answer questions — edit source files to fix the problem.
Do not modify existing tests unless the issue explicitly requires it.
Do not browse live GitHub/Jira for the answer; use this repo and the text below.

<issue>
{issue}
</issue>
"""
