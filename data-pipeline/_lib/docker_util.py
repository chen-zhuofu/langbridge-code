"""Docker helpers for per-task images (``lb-task:<task_id>``)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import paths


def docker(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def image_exists(tag: str) -> bool:
    return docker(["image", "inspect", tag]).returncode == 0


def remove_image(tag: str) -> bool:
    """Remove a local image tag. Returns True if removed or already absent."""
    if not image_exists(tag):
        return True
    return docker(["rmi", "-f", tag]).returncode == 0


def build_task_image(task_id: str, context_dir: Path | None = None) -> str:
    tag = paths.task_image(task_id)
    context_dir = Path(context_dir or paths.task_dir(task_id))
    dockerfile = context_dir / "Dockerfile"
    if not dockerfile.exists():
        raise FileNotFoundError(dockerfile)
    if image_exists(tag):
        return tag
    result = docker(["build", "-t", tag, "-f", str(dockerfile), str(context_dir)])
    if result.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {tag}:\n"
            f"{(result.stdout or '')[-2000:]}\n{(result.stderr or '')[-2000:]}"
        )
    return tag


def render_python_dockerfile(repo: str, base_commit: str) -> str:
    """Deterministic Python/pytest task image recipe (no LLM).

    Bases on ``langbridge-bench:py312`` (git/uv/build-essential already installed)
    so task builds skip ``apt-get`` — avoids host clock-skew failures against
    Debian mirrors.
    """
    return f"""# Auto-generated task env for {repo}@{base_commit[:12]}
# Base image already has git/curl/ca-certificates/build-essential/uv.
FROM langbridge-bench:py312

ENV SETUPTOOLS_SCM_PRETEND_VERSION=9999.0.0

WORKDIR /work/repo
RUN git init -q \\
    && git remote add origin https://github.com/{repo}.git \\
    && git fetch -q --depth 1 origin {base_commit} \\
    && git checkout -q FETCH_HEAD \\
    && uv venv .refvenv \\
    && (uv pip install --python .refvenv/bin/python -e ".[dev,test,tests,testing]" pytest \\
        || uv pip install --python .refvenv/bin/python -e . pytest \\
        || uv pip install --python .refvenv/bin/python . pytest) \\
    && uv pip install --python .refvenv/bin/python ruff mypy bandit

# "Activate" the task venv: plain `python`/`pytest` resolve to the editable
# install, so agent edits under /work/repo take effect without reinstalling.
# The eval harness pins its own interpreter to an absolute path instead.
ENV PATH="/work/repo/.refvenv/bin:${{PATH}}"

# Task payload (specs) is mounted at runtime as /opt/lb/task.json — not baked in.
WORKDIR /work/repo
"""
