"""Collect SWE-Chat sessions into interactive pipeline candidates.

```bash
# Local parquet export (recommended if HF gated):
uv run python interactive-bench/data-pipeline/collect/collect.py \\
  --data-dir /path/to/swe-chat-parquet --limit 50

# HuggingFace datasets (requires accepted terms + login):
uv run python interactive-bench/data-pipeline/collect/collect.py --hf --limit 50
```

Filters applied here:
- drop Mind Changer persona
- require at least one checkpoint id
- require prompt_count >= 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.io_util import append_drop, load_jsonl, write_jsonl  # noqa: E402


DROP_PERSONAS = frozenset({"Mind Changer"})


def _has_python_files(files_touched: list) -> bool:
    for path in files_touched:
        name = str(path).replace("\\", "/").lower()
        if name.endswith(".py") or name.endswith(".pyi"):
            return True
        if "/tests/" in name or name.endswith("conftest.py"):
            return True
    return False


def _has_python_tests(files_touched: list) -> bool:
    for path in files_touched:
        name = str(path).replace("\\", "/").lower()
        base = name.rsplit("/", 1)[-1]
        if not (name.endswith(".py") or name.endswith(".pyi")):
            continue
        if (
            "/tests/" in name
            or "/test/" in name
            or base.startswith("test_")
            or base.endswith("_test.py")
            or base == "conftest.py"
        ):
            return True
    return False


def _parse_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            return [text]
    return []


def load_sessions_from_parquet(data_dir: Path):
    import pandas as pd

    path = data_dir / "sessions.parquet"
    if not path.exists():
        raise FileNotFoundError(f"missing {path}")
    return pd.read_parquet(path)


def load_sessions_from_hf():
    from datasets import load_dataset

    return load_dataset("SALT-NLP/SWE-chat", "sessions", split="train").to_pandas()


def session_to_row(row) -> dict | None:
    """Convert a sessions-table row to a collect candidate, or None to skip."""
    session_id = str(row.get("session_id") or "")
    repo = str(row.get("repo_id") or "")
    if not session_id or not repo:
        return None

    persona = row.get("user_persona")
    if persona in DROP_PERSONAS:
        return {"_drop": True, "task_id": session_id, "reason": f"persona={persona}"}

    checkpoint_ids = _parse_list(row.get("checkpoint_ids"))
    if not checkpoint_ids and row.get("canonical_checkpoint_pk"):
        checkpoint_ids = [str(row["canonical_checkpoint_pk"])]
    if not checkpoint_ids:
        return {"_drop": True, "task_id": session_id, "reason": "no checkpoint_ids"}

    prompt_count = row.get("prompt_count")
    if prompt_count is not None and int(prompt_count) < 1:
        return {"_drop": True, "task_id": session_id, "reason": "prompt_count < 1"}

    files_touched = _parse_list(row.get("files_touched"))

    task_id = f"{repo.replace('/', '__')}__{session_id[:8]}"
    prompt_intents = _parse_list(row.get("prompt_intents") or row.get("prompt_intent"))
    return {
        "task_id": task_id,
        "session_id": session_id,
        "repo": repo,
        "user_persona": persona,
        "canonical_checkpoint_pk": row.get("canonical_checkpoint_pk"),
        "checkpoint_ids": checkpoint_ids,
        "prompt_count": int(prompt_count) if prompt_count is not None else None,
        "duration_seconds": row.get("duration_seconds"),
        "files_touched": files_touched,
        "has_python": _has_python_files(files_touched),
        "has_python_tests": _has_python_tests(files_touched),
        "branch": row.get("branch"),
        "agent": row.get("agent"),
        "turn_count": row.get("turn_count"),
        "created_at": str(row.get("created_at") or "") or None,
        "prompt_intents": [str(x) for x in prompt_intents] if prompt_intents else [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="Directory with sessions.parquet")
    parser.add_argument("--hf", action="store_true", help="Load sessions from HuggingFace")
    parser.add_argument("--limit", type=int, default=0, help="Max new candidates to write")
    parser.add_argument(
        "--python-only",
        action="store_true",
        help="Keep only sessions that touch .py files (matches lb-interactive py312 images)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=paths.DEFAULT_COLLECT_JSONL,
        help="Output jsonl path",
    )
    parser.add_argument(
        "--drop",
        type=Path,
        default=paths.DEFAULT_COLLECT_DROP,
        help="Drop json path",
    )
    args = parser.parse_args()

    if not args.data_dir and not args.hf:
        print("provide --data-dir or --hf", file=sys.stderr)
        return 2

    existing_rows = {r["task_id"]: r for r in load_jsonl(args.out) if r.get("task_id")}
    df = load_sessions_from_hf() if args.hf else load_sessions_from_parquet(args.data_dir)
    print(f"loaded {len(df)} sessions")

    # Prefer sessions that already advertise Python tests — higher F2P yield.
    rows = [session_to_row(row) for _, row in df.iterrows()]
    rows = [r for r in rows if r is not None]
    rows.sort(
        key=lambda r: (
            0 if r.get("_drop") else 1,
            1 if r.get("has_python_tests") else 0,
            1 if r.get("has_python") else 0,
            int(r.get("prompt_count") or 0),
        ),
        reverse=True,
    )

    kept: list[dict] = []
    dropped = 0
    for item in rows:
        if args.limit and len(kept) >= args.limit:
            break
        if item.get("_drop"):
            append_drop(args.drop, item["task_id"], item["reason"])
            dropped += 1
            continue
        files_touched = item.get("files_touched") or []
        # Unknown touch set: keep and let enrich decide (files_touched is often incomplete).
        if args.python_only and files_touched and not item.get("has_python"):
            append_drop(args.drop, item["task_id"], "no python files_touched")
            dropped += 1
            continue
        if item["task_id"] in existing_rows:
            continue
        kept.append(item)
        existing_rows[item["task_id"]] = item

    write_jsonl(args.out, existing_rows.values(), append=False)
    print(f"wrote {len(kept)} new candidates; dropped {dropped}; total {len(existing_rows)}")
    print(f"out: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
