"""Workspaces reset must return to base_commit, not merely HEAD."""
import subprocess
from pathlib import Path

from langbridge_code.eval.langbridge_bench import Workspaces


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_workspaces_reset_returns_to_base_not_agent_head(tmp_path, monkeypatch):
    """After the agent commits a fix, grade/reset must wipe back to base_commit.

    Otherwise an empty candidate patch still sees the fixed tree and gt_pass
    becomes a false positive.
    """
    repo = tmp_path / "task-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "src.py").write_text("bug\n", encoding="utf-8")
    _git(repo, "add", "src.py")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Simulate agent commit on top of base.
    (repo / "src.py").write_text("fixed\n", encoding="utf-8")
    _git(repo, "add", "src.py")
    _git(repo, "commit", "-m", "agent fix")
    assert (repo / "src.py").read_text(encoding="utf-8") == "fixed\n"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != base

    # Skip real checkout/venv; reuse the hand-built repo as the workspace.
    monkeypatch.setattr(
        "langbridge_code.eval.langbridge_bench._ref",
        lambda: type("R", (), {"shallow_checkout": lambda *a, **k: None, "make_venv": lambda *a, **k: "py"})(),
    )
    ws = Workspaces(root=tmp_path / "ws")
    repo_dir = str(repo)
    ws._ready["demo"] = (repo_dir, "py")
    ws._base_by_repo[repo_dir] = base

    ws.reset(repo_dir)

    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == base
    assert (repo / "src.py").read_text(encoding="utf-8") == "bug\n"
