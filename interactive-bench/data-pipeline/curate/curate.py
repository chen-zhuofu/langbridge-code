"""Write eval specs under ``interactive-bench/data/specs/``.

```bash
uv run python interactive-bench/data-pipeline/curate/curate.py --limit 20
```
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.io_util import append_drop, load_json, load_jsonl, write_json, write_jsonl  # noqa: E402
from _lib.quality import (  # noqa: E402
    clean_user_text,
    f2p_fingerprint,
    filter_intents,
    oracle_aligned,
    oracle_token_hits,
)
from _lib.spec import build_interactive_spec  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--in", dest="inp", type=Path, default=paths.DEFAULT_REFERENCE_JSONL)
    parser.add_argument("--out", type=Path, default=paths.DEFAULT_CURATE_JSONL)
    parser.add_argument("--drop", type=Path, default=paths.DEFAULT_CURATE_DROP)
    args = parser.parse_args()

    human_drop = set()
    if paths.DEFAULT_HUMAN_DROP.exists():
        for entry in load_json(paths.DEFAULT_HUMAN_DROP).get("dropped") or []:
            if isinstance(entry, dict) and entry.get("task_id"):
                human_drop.add(entry["task_id"])

    done = {r["task_id"] for r in load_jsonl(args.out) if r.get("task_id")}
    if args.drop.exists():
        for entry in load_json(args.drop).get("dropped") or []:
            if isinstance(entry, dict) and entry.get("task_id"):
                done.add(entry["task_id"])

    # Fingerprints already accepted (this run + prior curate out).
    seen_f2p = {
        f2p_fingerprint(
            r.get("fail_to_pass") or r.get("FAIL_TO_PASS") or [],
            repo=str(r.get("repo") or ""),
        )
        for r in load_jsonl(args.out)
        if r.get("task_id")
    }

    paths.SPECS_DIR.mkdir(parents=True, exist_ok=True)
    kept: list[dict] = []
    for inst in load_jsonl(args.inp):
        if args.limit and len(kept) >= args.limit:
            break
        tid = inst.get("task_id")
        if not tid or tid in done:
            continue
        if tid in human_drop:
            append_drop(args.drop, tid, "human drop")
            done.add(tid)
            continue
        f2p = list(inst.get("fail_to_pass") or inst.get("FAIL_TO_PASS") or [])
        if not f2p:
            append_drop(args.drop, tid, "missing F2P at curate")
            done.add(tid)
            continue
        intents = filter_intents(inst.get("intents") or [])
        if not intents:
            append_drop(args.drop, tid, "missing intents")
            done.add(tid)
            continue

        instruction = clean_user_text(inst.get("instruction") or "")
        if not instruction and intents:
            instruction = intents[0]["text"]

        if not oracle_aligned(
            instruction=instruction,
            intents=intents,
            test_files=inst.get("test_files") or [],
            fail_to_pass=f2p,
            test_patch=inst.get("test_patch") or "",
        ):
            hits = oracle_token_hits(
                instruction=instruction,
                intents=intents,
                test_files=inst.get("test_files") or [],
                fail_to_pass=f2p,
                test_patch=inst.get("test_patch") or "",
            )
            append_drop(
                args.drop,
                tid,
                f"oracle not aligned with instruction (hits={sorted(hits)[:8]})",
            )
            done.add(tid)
            print(f"  drop {tid}: oracle not aligned with instruction")
            continue

        fp = f2p_fingerprint(f2p, repo=str(inst.get("repo") or ""))
        if fp in seen_f2p:
            append_drop(args.drop, tid, f"duplicate F2P fingerprint {fp[:12]}")
            done.add(tid)
            print(f"  drop {tid}: duplicate F2P")
            continue

        row = dict(inst)
        row["instruction"] = instruction
        row["intents"] = intents
        row["fail_to_pass"] = f2p
        row["f2p_fingerprint"] = fp

        spec = build_interactive_spec(row)
        if row.get("test_files"):
            spec["test_files"] = row["test_files"]
        write_json(paths.spec_path(tid), spec)
        kept.append(row)
        done.add(tid)
        seen_f2p.add(fp)
        print(f"  ok {tid} → {paths.spec_path(tid)}")

    existing = {r["task_id"]: r for r in load_jsonl(args.out) if r.get("task_id")}
    for r in kept:
        existing[r["task_id"]] = r
    write_jsonl(args.out, existing.values(), append=False)
    print(f"curate {len(kept)} new; total {len(existing)}; specs={paths.SPECS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
