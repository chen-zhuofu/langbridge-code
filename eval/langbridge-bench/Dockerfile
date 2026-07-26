# Eval worker image: shared OS/Python tools preinstalled.
# Avoids per-task apt/curl installs (fragile HTTPS / clock skew).
# Per-repo packages still install into each task's .refvenv at setup time.
FROM python:3.12-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/usr/local/bin:/root/.local/bin:${PATH}"

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        git \
        curl \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# PyPI install — do not use astral.sh install.sh at container start.
RUN pip install --no-cache-dir uv \
    && uv pip install --system --no-cache \
        httpx openai numpy prompt-toolkit textual pytest

WORKDIR /work
