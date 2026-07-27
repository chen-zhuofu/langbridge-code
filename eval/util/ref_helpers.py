"""Shared patch/pytest helpers used by Docker grade + pipeline reference_test."""
from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

BUILD_ENV = {**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": "9999.0.0"}
TEST_EXTRA_NAMES = {"dev", "test", "tests", "testing"}
TEST_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
PYTEST_LINE_RE = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR)\b")


def run(args, cwd=None, timeout=None, check=False, env=None):
    return subprocess.run(
        args, cwd=cwd, timeout=timeout, check=check, capture_output=True, text=True, env=env
    )


def test_files_in_patch(test_patch: str) -> list[str]:
    return TEST_FILE_RE.findall(test_patch or "")


def apply_patch(repo_dir, patch_text, check_only=False):
    flags = ["--check"] if check_only else []
    result = subprocess.run(
        ["git", "apply", *flags, "--whitespace=nowarn"],
        cwd=repo_dir,
        input=patch_text,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stderr.strip()


def test_extras(repo_dir: Path) -> list[str]:
    pyproject = Path(repo_dir) / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    groups = data.get("project", {}).get("optional-dependencies", {})
    return [name for name in groups if name.lower() in TEST_EXTRA_NAMES]


def make_venv(repo_dir: Path):
    repo_dir = Path(repo_dir)
    venv = repo_dir / ".refvenv"
    run(["uv", "venv", str(venv)], cwd=repo_dir, check=True)
    py = venv / "bin" / "python"
    extras = test_extras(repo_dir)
    target = f".[{','.join(extras)}]" if extras else "."
    install = run(
        ["uv", "pip", "install", "--python", str(py), "-e", target, "pytest"],
        cwd=repo_dir,
        timeout=900,
        env=BUILD_ENV,
    )
    if install.returncode != 0:
        run(
            ["uv", "pip", "install", "--python", str(py), ".", "pytest"],
            cwd=repo_dir,
            timeout=900,
            env=BUILD_ENV,
        )
    return py


def run_pytest(py, repo_dir, test_files, timeout):
    if not test_files:
        return {}
    args = [
        str(py),
        "-m",
        "pytest",
        "-v",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-p",
        "pytester",
        "-o",
        "addopts=",
        "-o",
        "minversion=0",
        *test_files,
    ]
    result = run(args, cwd=repo_dir, timeout=timeout, env=BUILD_ENV)
    outcomes = {}
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        match = PYTEST_LINE_RE.match(line.strip())
        if match:
            outcomes[match.group(1)] = match.group(2)
    return outcomes
