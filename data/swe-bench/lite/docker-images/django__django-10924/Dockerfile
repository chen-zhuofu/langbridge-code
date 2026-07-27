# Adapter: SWE-bench official image → langbridge-bench lb-task layout.
# Upstream already has /testbed at base_commit + conda env ``testbed``.
FROM lb-swebench-harness:py312 AS harness
FROM swebench/sweb.eval.x86_64.django_1776_django-10924:latest

USER root

# langbridge-bench expects the repo at /work/repo with a .refvenv for grading.
RUN mkdir -p /work \
    && ln -sfn /testbed /work/repo \
    && mkdir -p /work/repo/.refvenv/bin \
    && ln -sfn /opt/miniconda3/envs/testbed/bin/python /work/repo/.refvenv/bin/python \
    && ln -sfn /opt/miniconda3/envs/testbed/bin/python /work/repo/.refvenv/bin/python3 \
    && (ln -sfn /opt/miniconda3/envs/testbed/bin/pytest /work/repo/.refvenv/bin/pytest || true)

# Copy Python 3.12 + harness deps from shared layer (avoid reinstall per task).
COPY --from=harness /usr/local /usr/local
COPY --from=harness /root/.local /root/.local

# Django SWE-bench images often lack pytest in the conda env; grading needs it.
RUN /opt/miniconda3/envs/testbed/bin/python -c "import pytest" 2>/dev/null \
    || /opt/miniconda3/envs/testbed/bin/pip install -q pytest \
    && mkdir -p /work/repo/.refvenv/bin \
    && ln -sfn /opt/miniconda3/envs/testbed/bin/python /work/repo/.refvenv/bin/python \
    && ln -sfn /opt/miniconda3/envs/testbed/bin/python /work/repo/.refvenv/bin/python3 \
    && ln -sfn /opt/miniconda3/envs/testbed/bin/pytest /work/repo/.refvenv/bin/pytest

ENV PATH="/usr/local/bin:/root/.local/bin:/opt/miniconda3/envs/testbed/bin:${PATH}"
WORKDIR /work/repo
