"""Docker helpers for ``lb-interactive:<task_id>`` images."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import paths


def docker(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def image_exists(tag: str) -> bool:
    return docker(["image", "inspect", tag]).returncode == 0


def image_repo_head(tag: str) -> str | None:
    """Return ``git rev-parse HEAD`` inside an existing task image, if any."""
    if not image_exists(tag):
        return None
    result = docker(
        ["run", "--rm", tag, "git", "-C", "/work/repo", "rev-parse", "HEAD"],
        timeout=60,
    )
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def build_task_image(
    task_id: str,
    context_dir: Path | None = None,
    *,
    base_commit: str | None = None,
    force: bool = False,
) -> str:
    tag = paths.task_image(task_id)
    context_dir = Path(context_dir or (paths.DOCKER_IMAGES_DIR / task_id))
    dockerfile = context_dir / "Dockerfile"
    if not dockerfile.exists():
        raise FileNotFoundError(dockerfile)

    if not force and image_exists(tag):
        if base_commit:
            head = image_repo_head(tag)
            if head and (
                head == base_commit
                or head.startswith(base_commit)
                or base_commit.startswith(head)
            ):
                return tag
            # Stale image from a previous base pin — rebuild.
        else:
            return tag

    result = docker(["build", "-t", tag, "-f", str(dockerfile), str(context_dir)])
    if result.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {tag}:\n"
            f"{(result.stdout or '')[-2000:]}\n{(result.stderr or '')[-2000:]}"
        )
    return tag


def render_python_dockerfile(repo: str, base_commit: str) -> str:
    return f"""# Interactive-bench task env for {repo}@{base_commit[:12]}
FROM langbridge-bench:py312

ENV SETUPTOOLS_SCM_PRETEND_VERSION=9999.0.0
ENV POETRY_VIRTUALENVS_CREATE=false

RUN git config --global user.email "agent@langbridge.local" \\
    && git config --global user.name "LangBridge Agent"

WORKDIR /work/repo
RUN git init -q \\
    && git remote add origin https://github.com/{repo}.git \\
    && git fetch -q origin {base_commit} \\
    && git checkout -q FETCH_HEAD \\
    && uv venv .refvenv \\
    && . .refvenv/bin/activate \\
    && (uv pip install -e ".[dev,test,tests,testing]" pytest \\
        || uv pip install -e . pytest \\
        || uv pip install . pytest \\
        || true) \\
    && uv pip install poetry pytest ruff mypy bandit \\
       fastapi 'uvicorn[standard]' httpx starlette pydantic \\
       python-dotenv python-multipart duckdb \\
    && for d in . backend src lib apps packages python server api; do \\
         if [ -f "$d/pyproject.toml" ]; then \\
           uv pip install -e "$d[dev,test,tests,testing]" || uv pip install -e "$d" || uv pip install "$d" || true; \\
           if grep -q '\\[tool.poetry\\]' "$d/pyproject.toml"; then \\
             (cd "$d" && poetry install --no-root --no-interaction) || true; \\
           fi; \\
         fi; \\
       done

ENV PATH="/work/repo/.refvenv/bin:${{PATH}}"
WORKDIR /work/repo
"""
