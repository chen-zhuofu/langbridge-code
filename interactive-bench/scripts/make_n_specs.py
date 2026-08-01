"""Push pipeline until N curated specs exist (skip demo).

```bash
GITHUB_TOKEN=... OPENAI_API_KEY=... OPENAI_BASE_URL=https://api.deepseek.com \\
  LB_INTERACTIVE_MODEL=deepseek-chat \\
  uv run python interactive-bench/scripts/make_n_specs.py --n 5 --data-dir data/swe-chat
```
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "interactive-bench"
PIPELINE = BENCH / "data-pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402


def _ids(jsonl: Path) -> set[str]:
    if not jsonl.exists():
        return set()
    return {
        json.loads(line)["task_id"]
        for line in jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _drops(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        e["task_id"]
        for e in (data.get("dropped") or [])
        if isinstance(e, dict) and e.get("task_id")
    }


def curated_real() -> list[str]:
    paths.SPECS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(paths.SPECS_DIR.glob("*.json")):
        if path.name.startswith("example__"):
            continue
        out.append(path.stem)
    return out


def pending(upstream: Path, downstream: Path, drop: Path) -> set[str]:
    return _ids(upstream) - _ids(downstream) - _drops(drop)


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--max-rounds", type=int, default=120)
    args = parser.parse_args()
    data = str(args.data_dir)
    py = sys.executable

    for round_i in range(args.max_rounds):
        have = curated_real()
        print(f"\n=== round {round_i+1}: curated={len(have)}/{args.n} {have} ===", flush=True)
        if len(have) >= args.n:
            return 0

        if pending(paths.DEFAULT_REFERENCE_JSONL, paths.DEFAULT_CURATE_JSONL, paths.DEFAULT_CURATE_DROP):
            # Intent LLM runs inside curate (after env/reference gates).
            run(
                [
                    py,
                    str(PIPELINE / "curate/curate.py"),
                    "--data-dir",
                    data,
                    "--limit",
                    "1",
                ]
            )
            continue
        if pending(paths.DEFAULT_ENV_JSONL, paths.DEFAULT_REFERENCE_JSONL, paths.DEFAULT_REFERENCE_DROP):
            run([py, str(PIPELINE / "reference/reference_test.py"), "--limit", "1"])
            continue
        if pending(paths.DEFAULT_ENRICH_JSONL, paths.DEFAULT_ENV_JSONL, paths.DEFAULT_ENV_DROP):
            run([py, str(PIPELINE / "env/build_env.py"), "--limit", "1"])
            continue
        if pending(paths.DEFAULT_RESOLVE_JSONL, paths.DEFAULT_ENRICH_JSONL, paths.DEFAULT_ENRICH_DROP):
            run([py, str(PIPELINE / "enrich/enrich.py"), "--data-dir", data, "--limit", "5"])
            continue

        before = len(_ids(paths.DEFAULT_RESOLVE_JSONL))
        rc = run([py, str(PIPELINE / "resolve/resolve.py"), "--data-dir", data, "--limit", "8"])
        if rc != 0:
            return rc
        if len(_ids(paths.DEFAULT_RESOLVE_JSONL)) == before:
            # Also check resolve drops grew — still may need collect
            before_c = len(_ids(paths.DEFAULT_COLLECT_JSONL))
            run(
                [
                    py,
                    str(PIPELINE / "collect/collect.py"),
                    "--data-dir",
                    data,
                    "--python-only",
                    "--limit",
                    "50",
                ]
            )
            if len(_ids(paths.DEFAULT_COLLECT_JSONL)) == before_c:
                print("no more collectable sessions", file=sys.stderr)
                return 1

    print("max rounds reached", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
