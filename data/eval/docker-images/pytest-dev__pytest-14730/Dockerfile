# Auto-generated task env for pytest-dev/pytest@85003621822f
# Base image already has git/curl/ca-certificates/build-essential/uv.
FROM langbridge-bench:py312

ENV SETUPTOOLS_SCM_PRETEND_VERSION=9999.0.0

WORKDIR /work/repo
RUN git init -q \
    && git remote add origin https://github.com/pytest-dev/pytest.git \
    && git fetch -q --depth 1 origin 85003621822f9c10063940068ccacc9c12b8c73f \
    && git checkout -q FETCH_HEAD \
    && uv venv .refvenv \
    && (uv pip install --python .refvenv/bin/python -e ".[dev,test,tests,testing]" pytest \
        || uv pip install --python .refvenv/bin/python -e . pytest \
        || uv pip install --python .refvenv/bin/python . pytest) \
    && uv pip install --python .refvenv/bin/python ruff mypy bandit

# Task payload (specs) is mounted at runtime as /opt/lb/task.json — not baked in.
WORKDIR /work/repo
