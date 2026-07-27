"""GitHub / local-git helpers for parent SHA and default-branch reachability."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def api_get(url: str) -> dict[str, Any] | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "langbridge-interactive-pipeline",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in {403, 429, 502, 503} and attempt < 4:
                import time

                time.sleep(2 ** attempt)
                last_err = exc
                continue
            raise
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt < 4:
                import time

                time.sleep(2 ** attempt)
                last_err = exc
                continue
            raise
    if last_err:
        raise last_err
    return None


@lru_cache(maxsize=2048)
def default_branch(repo: str) -> str | None:
    data = api_get(f"https://api.github.com/repos/{repo}")
    if not data:
        return None
    return data.get("default_branch")


@lru_cache(maxsize=4096)
def branch_tip(repo: str, branch: str) -> str | None:
    data = api_get(f"https://api.github.com/repos/{repo}/commits/{branch}")
    if not data:
        return None
    return data.get("sha")


@lru_cache(maxsize=8192)
def commit_parents(repo: str, sha: str) -> tuple[str, ...]:
    data = api_get(f"https://api.github.com/repos/{repo}/commits/{sha}")
    if not data:
        return ()
    return tuple(p["sha"] for p in data.get("parents") or [] if p.get("sha"))


def first_parent(repo: str, sha: str) -> str | None:
    parents = commit_parents(repo, sha)
    return parents[0] if parents else None


@lru_cache(maxsize=4096)
def _compare(repo: str, base: str, head: str) -> dict[str, Any] | None:
    """GitHub compare: status one of ahead/behind/identical/diverged."""
    return api_get(f"https://api.github.com/repos/{repo}/compare/{base}...{head}")


def api_get_raw(url: str, *, accept: str) -> str | None:
    """GET returning raw text (e.g. unified diff)."""
    headers = {
        "Accept": accept,
        "User-Agent": "langbridge-interactive-pipeline",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def compare_diff(repo: str, base: str, head: str) -> str | None:
    """Unified diff for ``base...head`` via GitHub compare Accept header."""
    url = f"https://api.github.com/repos/{repo}/compare/{base}...{head}"
    return api_get_raw(url, accept="application/vnd.github.v3.diff")


def compare_files(repo: str, base: str, head: str) -> list[str]:
    """Changed file paths for ``base...head``."""
    data = _compare(repo, base, head)
    if not data:
        return []
    return [f["filename"] for f in (data.get("files") or []) if f.get("filename")]


def compare_commits(repo: str, base: str, head: str) -> list[str]:
    """Commit SHAs on ``base...head`` (oldest → newest). Caps at GitHub's 250."""
    data = _compare(repo, base, head)
    if not data:
        return []
    out: list[str] = []
    for row in data.get("commits") or []:
        sha = row.get("sha")
        if sha:
            out.append(str(sha))
    return out


def compare_ahead_by(repo: str, base: str, head: str) -> int | None:
    data = _compare(repo, base, head)
    if not data:
        return None
    ahead = data.get("ahead_by")
    return int(ahead) if ahead is not None else None


def is_ancestor_api(repo: str, sha: str, tip: str) -> bool:
    """True if ``sha`` is equal to tip or an ancestor of tip."""
    if sha == tip:
        return True
    data = _compare(repo, sha, tip)
    if not data:
        return False
    # sha...tip: if tip contains sha, status is ahead or identical
    status = data.get("status")
    return status in {"ahead", "identical"}


class LocalGit:
    """Optional local clone for faster ancestry / parent queries."""

    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir)

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo_dir), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: {(result.stderr or '').strip()}"
            )
        return (result.stdout or "").strip()

    def first_parent(self, sha: str) -> str | None:
        try:
            out = self._run("rev-parse", f"{sha}^")
        except RuntimeError:
            return None
        return out or None

    def is_ancestor(self, sha: str, tip: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(self.repo_dir), "merge-base", "--is-ancestor", sha, tip],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def diff_name_only(self, base: str, gold: str) -> list[str]:
        out = self._run("diff", "--name-only", f"{base}...{gold}")
        return [line for line in out.splitlines() if line.strip()]

    def diff_patch(self, base: str, gold: str) -> str:
        return self._run("diff", f"{base}...{gold}")
