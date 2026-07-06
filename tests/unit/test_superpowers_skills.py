from langbridge_code.skills import list_skills, load_skill


def test_superpowers_skills_are_discoverable():
    names = {name for name, _ in list_skills()}
    assert "test-driven-development" in names
    assert "verification-before-completion" in names


def test_superpowers_skill_has_body():
    body = load_skill("test-driven-development")
    assert "test" in body.lower()
    assert len(body) > 100
