from langbridge_code.skills import list_skills, load_skill


def test_agent_skills_are_discoverable():
    names = {name for name, _ in list_skills()}
    assert "superpowers_test-driven-development" in names
    assert "superpowers_systematic-debugging" in names
    # Karpathy guidance is inlined in the worker system prompt, not a skill.
    assert "karpathy_think-before-coding" not in names
    assert "karpathy_surgical-changes" not in names


def test_superpowers_skill_has_body():
    body = load_skill("superpowers_test-driven-development")
    assert "test" in body.lower()
    assert len(body) > 100


def test_list_skills_for_role():
    planner_names = {name for name, _ in list_skills("planner")}
    assert "superpowers_brainstorming" in planner_names
    assert "superpowers_test-driven-development" not in planner_names

    langbridge_names = {name for name, _ in list_skills("langbridge")}
    assert "grilling" in langbridge_names
    assert "writing-simple-plans" in langbridge_names

    explorer_names = {name for name, _ in list_skills("explorer")}
    assert explorer_names == set()

    worker_names = {name for name, _ in list_skills("worker_coder")}
    assert "superpowers_test-driven-development" in worker_names
    assert "superpowers_using-git-worktrees" not in worker_names

    reviewer_names = {name for name, _ in list_skills("reviewer_code")}
    assert "clean-code-guard" in reviewer_names
    assert "test-guard" in reviewer_names
    assert "docs-guard" in reviewer_names


def test_guard_skill_reference_loads():
    body = load_skill("clean-code-guard")
    assert "LangBridge Code mapping (reviewer)" in body
    assert "Review mode" in body

    ref = load_skill("clean-code-guard/references/ai-failure-modes.md")
    assert "failure" in ref.lower()
    assert len(ref) > 100
