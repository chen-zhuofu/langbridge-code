"""Static-analysis scoring for Docker grade (ruff / mypy / bandit).

Delta-based: analyzers run only on the .py files the candidate diff touches,
once BEFORE the diff is applied (baseline) and once AFTER. Only findings the
candidate introduces count against the score — pre-existing repo issues and
repo-wide config quirks (e.g. mypy duplicate-module crashes on doc trees) do
not penalize the agent.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_STATIC_COMMANDS = ("ruff", "mypy", "bandit")

_DIFF_PATH_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def resolve_static_commands(spec: dict) -> list[str] | None:
    """Return analyzer commands for this task, or None to skip.

    Global default is ruff/mypy/bandit for every task. Per-spec overrides:
      - omit / null → use DEFAULT_STATIC_COMMANDS
      - false → skip
      - {"commands": [...]} → those commands (empty list skips)
    """
    if "static_analysis" not in spec:
        return list(DEFAULT_STATIC_COMMANDS)
    cfg = spec.get("static_analysis")
    if cfg is False or cfg is None:
        return None
    if not isinstance(cfg, dict):
        return list(DEFAULT_STATIC_COMMANDS)
    commands = cfg.get("commands")
    if commands is None:
        return list(DEFAULT_STATIC_COMMANDS)
    commands = list(commands)
    return commands or None


def changed_py_files(diff_text: str) -> list[str]:
    """Repo-relative .py paths touched by a unified diff (excluding deletions)."""
    return sorted(
        {
            path
            for path in _DIFF_PATH_RE.findall(diff_text or "")
            if path.endswith(".py")
        }
    )


def static_baseline(repo_dir: str, spec: dict, changed_files: list[str]) -> dict[str, Any]:
    """Findings count per command on the touched files, pre-candidate."""
    commands = resolve_static_commands(spec)
    if not commands or not changed_files:
        return {}
    return {
        cmd: _count_findings(cmd, repo_dir, changed_files)[0]
        for cmd in commands
    }


def score_static_analysis(
    repo_dir: str,
    spec: dict,
    changed_files: list[str],
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score = share of analyzers that report no NEW findings vs the baseline."""
    commands = resolve_static_commands(spec)
    if not commands or not changed_files:
        return {"score": None, "max": 1.0, "results": [], "skipped": True}

    results = []
    scored = 0
    failed = 0
    for cmd in commands:
        after, output = _count_findings(cmd, repo_dir, changed_files)
        before = (baseline or {}).get(cmd)
        if after is None or before is None:
            # Tool missing or crashed (before and/or after) — recorded, not scored.
            results.append(
                {
                    "command": cmd,
                    "ok": None,
                    "reason": "tool_unusable",
                    "baseline": before,
                    "after": after,
                    "output": output,
                }
            )
            continue
        new = after - before
        ok = new <= 0
        scored += 1
        if not ok:
            failed += 1
        results.append(
            {
                "command": cmd,
                "ok": ok,
                "reason": "passed" if ok else "new_findings",
                "baseline": before,
                "after": after,
                "new": max(0, new),
                "output": "" if ok else output,
            }
        )

    score = None if not scored else round(max(0.0, 1.0 - failed / scored), 3)
    return {
        "score": score,
        "max": 1.0,
        "results": results,
        "files": changed_files,
        "skipped": False,
    }


def _resolve_static_exe(name: str, repo_dir: str) -> str | None:
    """Prefer task ``.refvenv/bin`` (pipeline install), then PATH."""
    venv_exe = Path(repo_dir) / ".refvenv" / "bin" / name
    if venv_exe.is_file() and os.access(venv_exe, os.X_OK):
        return str(venv_exe)
    return shutil.which(name)


def _count_findings(name: str, repo_dir: str, files: list[str]) -> tuple[int | None, str]:
    """Findings count for one analyzer on the given files; None = unusable."""
    exe = _resolve_static_exe(name, repo_dir)
    if exe is None:
        return None, "not_installed"

    present = [f for f in files if (Path(repo_dir) / f).is_file()]
    if not present:
        return 0, ""

    if name == "ruff":
        argv = [exe, "check", "--exit-zero", "--output-format", "concise", *present]
    elif name == "mypy":
        argv = [exe, "--no-error-summary", "--follow-imports=silent", *present]
    elif name == "bandit":
        argv = [exe, "-q", "-f", "json", *present]
    else:
        argv = [exe, *present]

    try:
        proc = subprocess.run(
            argv,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as error:  # noqa: BLE001
        return None, str(error)[:500]

    tail = ((proc.stdout or "") + (proc.stderr or ""))[-1000:]

    if name == "ruff":
        if proc.returncode != 0:
            return None, tail
        count = sum(1 for line in (proc.stdout or "").splitlines() if line.strip())
        return count, tail
    if name == "mypy":
        count = sum(1 for line in (proc.stdout or "").splitlines() if ": error:" in line)
        if proc.returncode not in (0, 1) and count == 0:
            return None, tail  # crash/config error, no checking happened
        return count, tail
    if name == "bandit":
        try:
            return len(json.loads(proc.stdout or "{}").get("results", [])), tail
        except json.JSONDecodeError:
            return None, tail
    return (0 if proc.returncode == 0 else None), tail
