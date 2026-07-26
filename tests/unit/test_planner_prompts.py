from langbridge_code.agents.planner import initial_plan_prompt


def test_initial_plan_prompt_uses_plain_checkboxes():
    prompt = initial_plan_prompt("Build auth system")
    assert "coding" in prompt.lower()
    assert "slide" not in prompt.lower()
    assert "- [ ] Task N: <reviewable deliverable>" in prompt
    assert "[coding]" not in prompt
    assert "plan_task_type" not in prompt.lower()


def test_initial_plan_prompt_requires_evidence_based_plan():
    prompt = initial_plan_prompt("Build a web Tetris game").lower()
    assert "out of scope" in prompt
    assert "key discoveries" in prompt
    assert "path:line" in prompt or "`path:line`" in prompt
    assert "verify:" in prompt
    assert "changes required" in prompt
    assert "do not write the implementation" in prompt
    assert "no limit/offset" in prompt
    assert "padding" in prompt or "duplicate" in prompt


def test_initial_plan_prompt_requires_explicit_deps_note():
    prompt = initial_plan_prompt("Build auth system")
    assert "<!-- depends:" not in prompt
    assert "<!-- verify:" not in prompt
    assert "deps: none" in prompt
    assert "MANDATORY" in prompt


def test_initial_plan_prompt_requires_complete_task_contracts():
    prompt = initial_plan_prompt("Build auth system")
    for section in (
        "Objective:",
        "Detailed requirements:",
        "Acceptance spec:",
        "Deliverables:",
        "Verify:",
        "Out of scope:",
    ):
        assert section in prompt
    assert "Acceptance spec defines correct behavior" in prompt
    assert "contradictory" in prompt
