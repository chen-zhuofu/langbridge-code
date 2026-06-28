"""Eval runners for the five roles.

Each runner is pure orchestration over an injected agent callable and an injected
grader, so the scoring logic is unit-testable with stubs (no LLM, no repo). The
real callables that drive the actual L3/L4/L5/PM agents against a target repo live
in `agents_adapter.py`.
"""
