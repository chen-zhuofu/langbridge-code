from __future__ import annotations

import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[2] / "eval" / "data-pipeline"
sys.path.insert(0, str(PIPELINE))

from _lib.repo_balance import allocate  # noqa: E402
from collect.collect import parse_repos_md, repo_targets  # noqa: E402


def test_twenty_tasks_across_nine_repos():
    repos = [f"owner/repo-{index}" for index in range(9)]

    assert list(allocate(20, repos).values()) == [3, 3, 2, 2, 2, 2, 2, 2, 2]


def test_existing_progress_is_preserved_and_remaining_slots_stay_balanced():
    repos = ["owner/a", "owner/b", "owner/c"]
    progress = {
        "owner/a": {"done": 3, "pending": 0},
        "retired/repo": {"done": 1, "pending": 0},
    }

    assert repo_targets(7, repos, progress) == {
        "owner/a": 3,
        "owner/b": 2,
        "owner/c": 1,
    }


def test_exhausted_repo_share_is_redistributed():
    repos = ["owner/a", "owner/b", "owner/c"]
    progress = {
        "owner/a": {"done": 2, "pending": 0, "failed": 0},
        "owner/b": {"done": 0, "pending": 0, "failed": 2, "exhausted": True},
        "owner/c": {"done": 0, "pending": 0, "failed": 2, "exhausted": True},
    }

    assert repo_targets(6, repos, progress) == {
        "owner/a": 6,
        "owner/b": 0,
        "owner/c": 0,
    }


def test_failures_alone_do_not_abandon_a_repo():
    repos = ["owner/a", "owner/b"]
    progress = {
        "owner/a": {"done": 1, "pending": 0, "failed": 10},
        "owner/b": {"done": 0, "pending": 0, "failed": 10},
    }

    assert repo_targets(4, repos, progress) == {
        "owner/a": 2,
        "owner/b": 2,
    }


def test_parse_repos_md_ignores_html_comment_blocks(tmp_path):
    path = tmp_path / "repos.md"
    path.write_text(
        "owner/enabled\n<!--\nowner/disabled\n-->\nowner/also-enabled\n",
        encoding="utf-8",
    )

    assert parse_repos_md(path) == ["owner/enabled", "owner/also-enabled"]
