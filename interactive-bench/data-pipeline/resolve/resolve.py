"""Resolve base/gold commits for collected SWE-Chat sessions.

```bash
uv run python interactive-bench/data-pipeline/resolve/resolve.py \\
  --data-dir /path/to/swe-chat-parquet --limit 20
```

Needs ``checkpoints.parquet`` + ``commits.parquet`` next to sessions data,
plus ``GITHUB_TOKEN`` (or ``GH_TOKEN``) for parent / default-branch checks.
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
from _lib.github import branch_tip, default_branch, first_parent, is_ancestor_api  # noqa: E402
from _lib.io_util import append_drop, load_jsonl, write_jsonl  # noqa: E402
from _lib.quality import MAX_WINDOW_COMMITS  # noqa: E402
from _lib.resolve_commits import (  # noqa: E402
    collect_candidate_commits,
    resolve_gold_and_base,
)


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


def load_checkpoint_maps(data_dir: Path):
    import pandas as pd

    cps = pd.read_parquet(
        data_dir / "checkpoints.parquet",
        columns=["checkpoint_pk", "commit_shas"],
    )
    # commits.parquet is ~1GB with diffs — only load metadata columns.
    commits = pd.read_parquet(
        data_dir / "commits.parquet",
        columns=[
            "commit_sha",
            "commit_date",
            "author_date",
            "checkpoint_pk",
            "commit_index",
        ],
    )

    cp_to_shas: dict[str, list[str]] = {}
    for _, row in cps.iterrows():
        pk = str(row["checkpoint_pk"])
        cp_to_shas[pk] = [str(x) for x in _parse_list(row.get("commit_shas"))]

    commit_meta: dict[str, dict] = {}
    for _, row in commits.iterrows():
        sha = str(row["commit_sha"])
        commit_meta[sha] = {
            "commit_date": row.get("commit_date"),
            "author_date": row.get("author_date"),
            "checkpoint_pk": row.get("checkpoint_pk"),
            "commit_index": int(row["commit_index"])
            if row.get("commit_index") is not None and str(row.get("commit_index")) != "nan"
            else None,
        }
    return cp_to_shas, commit_meta


def resolve_one(inst: dict, cp_to_shas: dict, commit_meta: dict) -> dict:
    repo = inst["repo"]
    refs = collect_candidate_commits(
        inst.get("checkpoint_ids"),
        checkpoint_commit_shas=cp_to_shas,
        commit_meta=commit_meta,
    )
    branch = default_branch(repo)
    if not branch:
        return {**inst, "_drop": True, "reason": "cannot resolve default branch"}
    tip = branch_tip(repo, branch)
    if not tip:
        return {**inst, "_drop": True, "reason": f"cannot resolve tip of {branch}"}

    result = resolve_gold_and_base(
        refs,
        default_branch_tip=tip,
        is_ancestor=lambda sha, tip_sha: is_ancestor_api(repo, sha, tip_sha),
        parent_of=lambda sha: first_parent(repo, sha),
        max_span_commits=MAX_WINDOW_COMMITS,
    )
    if not result.ok:
        return {**inst, "_drop": True, "reason": result.reason}

    out = dict(inst)
    out.update(
        {
            "default_branch": branch,
            "default_branch_tip": tip,
            "base_commit": result.base_sha,
            "gold_commit": result.gold_sha,
            "first_commit_sha": result.first_sha,
            "last_commit_sha": result.last_sha,
            "candidate_commit_shas": [r.sha for r in result.candidates],
        }
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--in", dest="inp", type=Path, default=paths.DEFAULT_COLLECT_JSONL)
    parser.add_argument("--out", type=Path, default=paths.DEFAULT_RESOLVE_JSONL)
    parser.add_argument("--drop", type=Path, default=paths.DEFAULT_RESOLVE_DROP)
    args = parser.parse_args()

    cp_to_shas, commit_meta = load_checkpoint_maps(args.data_dir)
    print(f"checkpoints={len(cp_to_shas)} commits={len(commit_meta)}")

    done = {r["task_id"] for r in load_jsonl(args.out) if r.get("task_id")}
    # also skip previously dropped
    if args.drop.exists():
        from _lib.io_util import load_json

        for entry in (load_json(args.drop).get("dropped") or []):
            if isinstance(entry, dict) and entry.get("task_id"):
                done.add(entry["task_id"])

    kept: list[dict] = []
    existing = {r["task_id"]: r for r in load_jsonl(args.out) if r.get("task_id")}
    for inst in load_jsonl(args.inp):
        if args.limit and len(kept) >= args.limit:
            break
        tid = inst.get("task_id")
        if not tid or tid in done:
            continue
        try:
            resolved = resolve_one(inst, cp_to_shas, commit_meta)
        except Exception as exc:  # noqa: BLE001 — stage boundary
            append_drop(args.drop, tid, f"error: {exc}")
            done.add(tid)
            print(f"  drop {tid}: error: {exc}", flush=True)
            continue
        if resolved.get("_drop"):
            append_drop(args.drop, tid, resolved["reason"])
            done.add(tid)
            print(f"  drop {tid}: {resolved['reason']}", flush=True)
            continue
        kept.append(resolved)
        done.add(tid)
        existing[tid] = resolved
        write_jsonl(args.out, existing.values(), append=False)
        print(
            f"  ok {tid}: base={resolved['base_commit'][:12]} gold={resolved['gold_commit'][:12]}",
            flush=True,
        )

    print(f"resolved {len(kept)} new; total {len(existing)}; out={args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
