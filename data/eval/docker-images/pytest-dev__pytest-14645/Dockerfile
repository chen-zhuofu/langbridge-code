# Auto-generated task env for pytest-dev/pytest@35f21fdcfc9b
# Base image already has git/curl/ca-certificates/build-essential/uv.
FROM langbridge-bench:py312

ENV SETUPTOOLS_SCM_PRETEND_VERSION=9999.0.0

WORKDIR /work/repo
RUN git init -q \
    && git remote add origin https://github.com/pytest-dev/pytest.git \
    && git fetch -q --depth 1 origin 35f21fdcfc9b5817a2aafc449dc5cff5aba83725 \
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
