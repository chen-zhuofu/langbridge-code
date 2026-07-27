# Auto-generated task env for NousResearch/hermes-agent@e369d6ea3f8d
# Base image already has git/curl/ca-certificates/build-essential/uv.
FROM langbridge-bench:py312

ENV SETUPTOOLS_SCM_PRETEND_VERSION=9999.0.0

# Git identity so agents can commit without configuring one themselves.
RUN git config --global user.email "agent@langbridge.local" \
    && git config --global user.name "LangBridge Agent"

# Full-history fetch of the base commit: agents get `git log`/`git diff`
# against real history instead of a single orphan commit (which pushed them
# to GitHub for context). Fetching only the commit — never tags or branch
# heads — keeps post-base_commit work (including the gold fix) out of the
# image.
WORKDIR /work/repo
RUN git init -q \
    && git remote add origin https://github.com/NousResearch/hermes-agent.git \
    && git fetch -q origin e369d6ea3f8ded58012618ac55300cdb1fc24b8d \
    && git checkout -q FETCH_HEAD \
    && uv venv .refvenv \
    && (uv pip install --python .refvenv/bin/python -e ".[dev,test,tests,testing]" pytest \
        || uv pip install --python .refvenv/bin/python -e . pytest \
        || uv pip install --python .refvenv/bin/python . pytest) \
    && uv pip install --python .refvenv/bin/python ruff mypy bandit

# "Activate" the task venv: plain `python`/`pytest` resolve to the editable
# install, so agent edits under /work/repo take effect without reinstalling.
# The eval harness pins its own interpreter to an absolute path instead.
ENV PATH="/work/repo/.refvenv/bin:${PATH}"

# Task payload (specs) is mounted at runtime as /opt/lb/task.json — not baked in.
WORKDIR /work/repo
