# Shared harness layer for SWE-bench → lb-task adapters.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && export PATH="/root/.local/bin:$PATH" \
    && ln -sfn /root/.local/bin/uv /usr/local/bin/uv \
    && uv pip install --system openai httpx numpy prompt_toolkit textual pytest
