# SWE-bench → langbridge-bench style

Specs and Docker adapters for running SWE-bench instances through
`eval/run_eval.py` with `--bench-dir`.

```text
data/swe-bench/
  lite/
    specs/           # langbridge task JSON
    docker-images/   # Dockerfile per task
    drop/drop.json
  verified/
    specs/
    docker-images/
    drop/drop.json
  pro/
    specs/
    docker-images/
    drop/drop.json
```

## Import / build

```bash
# Lite (easy) — prefer local official images when available
uv run python eval/import_to_langbridge.py --difficulty lite --prefer-local --pull --count 10

# Verified (medium)
uv run python eval/import_to_langbridge.py --difficulty verified --prefer-local --pull --count 10

# Pro (hard) — uses jefzda/sweap-images on Docker Hub
uv run python eval/import_to_langbridge.py --difficulty pro --pull --count 10
```

## Eval

```bash
uv run python eval/run_eval.py --bench-dir data/swe-bench/lite --limit 10 --workers 2 --open-network
uv run python eval/run_eval.py --bench-dir data/swe-bench/verified --limit 10 --workers 2 --open-network
uv run python eval/run_eval.py --bench-dir data/swe-bench/pro --limit 10 --workers 2 --open-network
```

`--open-network` is currently needed for reliable LLM egress from SWE-bench
adapter images (default `lb-eval-proxy` path often yields empty patches).
Results with `--open-network` are not network-guard valid.

Pro tasks use `/app` (not `/testbed`) and may be non-Python repos; grading
follows Scale's harness rather than the standard swebench grader.
