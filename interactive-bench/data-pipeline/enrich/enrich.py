"""Enrich resolved sessions: session diff, patch split, runtime, dirty check.

```bash
uv run python interactive-bench/data-pipeline/enrich/enrich.py \\
  --data-dir /path/to/swe-chat --limit 20
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
from _lib.conversations import load_conversation_turns, user_prompts_from_turns  # noqa: E402
from _lib.diff_split import changed_paths_from_diff, split_unified_diff  # noqa: E402
from _lib.github import (  # noqa: E402
    compare_ahead_by,
    compare_commits,
    compare_diff,
    compare_files,
    first_parent,
)
from _lib.io_util import append_drop, load_json, load_jsonl, write_jsonl  # noqa: E402
from _lib.labels import horizon_from_runtime, task_type_from_prompt_intents  # noqa: E402
from _lib.quality import (  # noqa: E402
    MAX_CODE_FILES,
    MAX_WINDOW_COMMITS,
    MIN_WINDOW_SCORE,
    clean_user_text,
    has_python_tests,
    is_thin_instruction,
    oracle_aligned,
    oracle_token_hits,
    pick_instruction,
    score_testful_window,
    test_patch_fingerprint,
)
from _lib.resolve_commits import files_outside_touched  # noqa: E402
from _lib.runtime import agent_runtime_seconds  # noqa: E402


def _split_ok(patch: str) -> tuple[str, str, list[str], list[str]] | None:
    test_patch, code_patch, test_files, code_files = split_unified_diff(patch)
    if not (test_patch or "").strip():
        return None
    if not has_python_tests(test_files):
        return None
    if not code_files or not (code_patch or "").strip():
        # Test-only windows cannot produce FAIL_TO_PASS (pre==post after empty code apply).
        return None
    if len(code_files) > MAX_CODE_FILES:
        return None
    return test_patch, code_patch, test_files, code_files


def tighten_base_window(
    repo: str,
    base: str,
    gold: str,
    *,
    instruction: str = "",
    followups: list[str] | None = None,
    files_touched: list[str] | None = None,
    max_commits: int = MAX_WINDOW_COMMITS,
) -> tuple[str, str, str, str, list[str], list[str], int, int] | None:
    """Pick the best suffix of base→gold that still has Python tests.

    Scores each candidate window by oracle/path overlap with the user ask so we
    do not keep the first testful tip slice when it is unrelated session fluff.
    Returns ``(..., window_n, score)``.
    """
    commits = compare_commits(repo, base, gold)
    ahead = compare_ahead_by(repo, base, gold)
    full_patch = compare_diff(repo, base, gold)
    if full_patch is None:
        return None

    candidates: list[tuple[int, str, str, str, str, list[str], list[str], int]] = []

    def _consider(candidate_base: str, patch: str, n: int) -> None:
        split = _split_ok(patch)
        if not split:
            return
        tp, cp, tf, cf = split
        score = score_testful_window(
            instruction=instruction,
            followups=followups,
            files_touched=files_touched,
            test_files=tf,
            code_files=cf,
            test_patch=tp,
            window_commits=n,
        )
        candidates.append((score, candidate_base, patch, tp, cp, tf, cf, n))

    # Always evaluate suffix windows; never blindly keep a long full-range diff.
    if commits:
        limit = min(len(commits), max_commits)
        for n in range(1, limit + 1):
            window = commits[-n:]
            first_sha = window[0]
            candidate_base = first_parent(repo, first_sha) or base
            patch = compare_diff(repo, candidate_base, gold)
            if patch is None:
                continue
            _consider(candidate_base, patch, n)
    else:
        n = ahead if ahead is not None else 1
        if n <= max_commits:
            _consider(base, full_patch, max(n, 1))

    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], -row[7]), reverse=True)
    score, candidate_base, patch, tp, cp, tf, cf, n = candidates[0]
    return candidate_base, patch, tp, cp, tf, cf, n, score


def _load_prompts(inst: dict, data_dir: Path | None) -> tuple[str, list[str], float | None, int | None]:
    agent_runtime = inst.get("agent_runtime_sec")
    turn_count = None
    raw_prompts: list[str] = []
    if data_dir is not None:
        turns = load_conversation_turns(data_dir, inst["session_id"])
        if turns:
            rt = agent_runtime_seconds(turns)
            if rt is not None:
                agent_runtime = rt
            raw_prompts = user_prompts_from_turns(turns)
            turn_count = len(turns)
    if not raw_prompts and inst.get("instruction"):
        raw_prompts = [str(inst.get("instruction") or "")]
        raw_prompts.extend(str(p) for p in (inst.get("followup_prompts") or []))
    instruction, followups = pick_instruction(raw_prompts, fallback=str(inst.get("instruction") or ""))
    return instruction, followups, (
        float(agent_runtime) if agent_runtime is not None else None
    ), turn_count


def enrich_one(
    inst: dict,
    *,
    data_dir: Path | None,
    seen_test_fps: set[str] | None = None,
) -> dict:
    repo = inst["repo"]
    base = inst["base_commit"]
    gold = inst["gold_commit"]
    tid = inst["task_id"]
    touched = list(inst.get("files_touched") or [])

    instruction, followups, agent_runtime, turn_count = _load_prompts(inst, data_dir)
    if is_thin_instruction(instruction):
        return {
            **inst,
            "_drop": True,
            "reason": f"thin instruction ({len(instruction)} chars)",
        }

    tightened = tighten_base_window(
        repo,
        base,
        gold,
        instruction=instruction,
        followups=followups,
        files_touched=touched,
    )
    if tightened is None:
        patch = compare_diff(repo, base, gold)
        if patch is None:
            return {**inst, "_drop": True, "reason": "cannot fetch diff base...gold"}
        if not patch.strip():
            return {**inst, "_drop": True, "reason": "empty diff base...gold"}
        test_patch, code_patch, test_files, code_files = split_unified_diff(patch)
        if not test_patch.strip():
            return {**inst, "_drop": True, "reason": "no test changes in session diff"}
        if not has_python_tests(test_files):
            return {
                **inst,
                "_drop": True,
                "reason": "no python test files (lb-interactive images are py312)",
            }
        if len(code_files) > MAX_CODE_FILES:
            return {
                **inst,
                "_drop": True,
                "reason": f"code_files too large for first-pass env ({len(code_files)})",
            }
        ahead = compare_ahead_by(repo, base, gold)
        return {
            **inst,
            "_drop": True,
            "reason": (
                f"no tight testful window within {MAX_WINDOW_COMMITS} commits "
                f"(ahead_by={ahead})"
            ),
        }

    new_base, patch, test_patch, code_patch, test_files, code_files, window_n, win_score = tightened
    base = new_base

    if win_score < MIN_WINDOW_SCORE:
        return {
            **inst,
            "_drop": True,
            "reason": f"window score too low ({win_score} < {MIN_WINDOW_SCORE})",
        }

    intents_proxy = [{"text": instruction}, *[{"text": p} for p in followups[:6]]]
    if not oracle_aligned(
        instruction=instruction,
        intents=intents_proxy,
        test_files=test_files,
        fail_to_pass=[],
        test_patch=test_patch,
    ):
        hits = oracle_token_hits(
            instruction=instruction,
            intents=intents_proxy,
            test_files=test_files,
            fail_to_pass=[],
            test_patch=test_patch,
        )
        return {
            **inst,
            "_drop": True,
            "reason": (
                f"oracle not aligned with instruction at enrich "
                f"(hits={sorted(hits)[:8]}, window={window_n}, score={win_score})"
            ),
        }

    code_hits = oracle_token_hits(
        instruction=instruction,
        intents=intents_proxy,
        test_files=code_files,
        fail_to_pass=[],
        test_patch=code_patch,
    )
    if not code_hits:
        return {
            **inst,
            "_drop": True,
            "reason": "instruction not aligned with code patch (likely wrong window)",
        }

    fp = test_patch_fingerprint(test_patch, repo=repo)
    if seen_test_fps is not None and fp in seen_test_fps:
        return {
            **inst,
            "_drop": True,
            "reason": f"duplicate test_patch fingerprint {fp[:12]}",
        }

    changed = changed_paths_from_diff(patch) or compare_files(repo, base, gold)
    dirty = files_outside_touched(changed, touched)
    if touched and dirty and len(dirty) >= len(changed):
        return {
            **inst,
            "_drop": True,
            "reason": f"no overlap with files_touched; extras={dirty[:8]}",
        }

    out = dict(inst)
    out["base_commit"] = base
    out["window_commits"] = window_n
    out["window_score"] = win_score
    out["test_patch_fingerprint"] = fp
    out["session_patch"] = patch
    out["test_patch"] = test_patch
    out["gold_code_patch"] = code_patch
    out["test_files"] = test_files
    out["code_files"] = code_files
    out["changed_files"] = changed
    if dirty:
        out["files_touched_extras"] = dirty[:50]

    out["agent_runtime_sec"] = agent_runtime
    out["instruction"] = instruction
    out["followup_prompts"] = [clean_user_text(p) for p in followups]
    if turn_count is not None:
        out["conversation_turn_count"] = turn_count
    out["horizon"] = horizon_from_runtime(agent_runtime)
    out["task_type"] = inst.get("task_type") or task_type_from_prompt_intents(
        inst.get("prompt_intents")
    )
    out["docker_image"] = paths.task_image(tid)
    if seen_test_fps is not None:
        seen_test_fps.add(fp)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="SWE-Chat parquet dir (for conversations)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--in", dest="inp", type=Path, default=paths.DEFAULT_RESOLVE_JSONL)
    parser.add_argument("--out", type=Path, default=paths.DEFAULT_ENRICH_JSONL)
    parser.add_argument("--drop", type=Path, default=paths.DEFAULT_ENRICH_DROP)
    args = parser.parse_args()

    done = {r["task_id"] for r in load_jsonl(args.out) if r.get("task_id")}
    if args.drop.exists():
        for entry in load_json(args.drop).get("dropped") or []:
            if isinstance(entry, dict) and entry.get("task_id"):
                done.add(entry["task_id"])

    seen_fps = {
        str(r.get("test_patch_fingerprint") or test_patch_fingerprint(r.get("test_patch") or "", repo=str(r.get("repo") or "")))
        for r in load_jsonl(args.out)
        if r.get("task_id") and (r.get("test_patch") or r.get("test_patch_fingerprint"))
    }

    kept: list[dict] = []
    for inst in load_jsonl(args.inp):
        if args.limit and len(kept) >= args.limit:
            break
        tid = inst.get("task_id")
        if not tid or tid in done:
            continue
        try:
            enriched = enrich_one(inst, data_dir=args.data_dir, seen_test_fps=seen_fps)
        except Exception as exc:  # noqa: BLE001
            append_drop(args.drop, tid, f"error: {exc}")
            done.add(tid)
            print(f"  drop {tid}: error: {exc}")
            continue
        if enriched.get("_drop"):
            append_drop(args.drop, tid, enriched["reason"])
            done.add(tid)
            print(f"  drop {tid}: {enriched['reason']}")
            continue
        kept.append(enriched)
        done.add(tid)
        print(
            f"  ok {tid}: tests={len(enriched.get('test_files') or [])} "
            f"code={len(enriched.get('code_files') or [])} "
            f"window={enriched.get('window_commits')} score={enriched.get('window_score')}"
        )

    existing = {r["task_id"]: r for r in load_jsonl(args.out) if r.get("task_id")}
    for r in kept:
        existing[r["task_id"]] = r
    write_jsonl(args.out, existing.values(), append=False)
    print(f"enrich {len(kept)} new; total {len(existing)}; out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
