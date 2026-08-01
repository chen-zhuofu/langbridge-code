# Interactive-bench task env for cyyeh/duckdb-data-agent@20520f335c22
FROM langbridge-bench:py312

ENV SETUPTOOLS_SCM_PRETEND_VERSION=9999.0.0
ENV POETRY_VIRTUALENVS_CREATE=false

RUN git config --global user.email "agent@langbridge.local" \
    && git config --global user.name "LangBridge Agent"

WORKDIR /work/repo
RUN git init -q \
    && git remote add origin https://github.com/cyyeh/duckdb-data-agent.git \
    && git fetch -q origin 20520f335c223118091b7ea34a79018f09ece0cd \
    && git checkout -q FETCH_HEAD \
    && uv venv .refvenv \
    && . .refvenv/bin/activate \
    && (uv pip install -e ".[dev,test,tests,testing]" pytest \
        || uv pip install -e . pytest \
        || uv pip install . pytest \
        || true) \
    && uv pip install poetry pytest ruff mypy bandit \
       fastapi 'uvicorn[standard]' httpx starlette pydantic \
       python-dotenv python-multipart duckdb \
    && for d in . backend src lib apps packages python server api; do \
         if [ -f "$d/pyproject.toml" ]; then \
           uv pip install -e "$d[dev,test,tests,testing]" || uv pip install -e "$d" || uv pip install "$d" || true; \
           if grep -q '\[tool.poetry\]' "$d/pyproject.toml"; then \
             (cd "$d" && poetry install --no-root --no-interaction) || true; \
           fi; \
         fi; \
       done

ENV PATH="/work/repo/.refvenv/bin:${PATH}"
WORKDIR /work/repo
