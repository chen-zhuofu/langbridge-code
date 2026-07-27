"""Unit tests for in-checkout grading used by Docker eval."""
from pathlib import Path


def test_grade_checkout_writes_json(tmp_path, monkeypatch):
    import grade_checkout

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".refvenv" / "bin").mkdir(parents=True)
    (repo / ".refvenv" / "bin" / "python").write_text("#!/bin/true\n", encoding="utf-8")

    monkeypatch.setattr(
        "util.ref_helpers.apply_patch",
        lambda repo_dir, patch_text: (True, ""),
    )
    monkeypatch.setattr(
        "util.ref_helpers.run_pytest",
        lambda py, repo_dir, test_files, timeout: {test_files[0]: "PASSED"},
    )
    monkeypatch.setattr(
        "util.dimensions.static_baseline",
        lambda repo_dir, spec, changed_files: {},
    )
    monkeypatch.setattr(
        "util.dimensions.score_static_analysis",
        lambda repo_dir, spec, changed_files, baseline: {"score": 1.0, "skipped": False},
    )

    spec = {
        "task_id": "t1",
        "test_patch": "diff --git a/t b/t\n",
        "test_files": ["tests/test_a.py::test_x"],
        "fail_to_pass": ["tests/test_a.py::test_x"],
        "pass_to_pass": [],
    }
    out = tmp_path / "grade.json"
    result = grade_checkout.grade_checkout(
        repo_dir=repo,
        spec=spec,
        candidate_diff="+fix\n",
        timeout=30,
    )
    assert result["resolved"] is True
    assert result["status"] == "graded"

    # CLI path
    spec_path = tmp_path / "spec.json"
    diff_path = tmp_path / "candidate.diff"
    spec_path.write_text(__import__("json").dumps(spec), encoding="utf-8")
    diff_path.write_text("+fix\n", encoding="utf-8")
    code = grade_checkout.main(
        ["--spec", str(spec_path), "--diff", str(diff_path), "--out", str(out), "--repo", str(repo)]
    )
    assert code == 0
    assert out.exists()
    assert __import__("json").loads(out.read_text(encoding="utf-8"))["resolved"] is True
