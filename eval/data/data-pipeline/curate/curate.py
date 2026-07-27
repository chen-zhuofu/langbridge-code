"""Stage 4 — curate: LLM keep / rewrite / drop, then kind + difficulty.

Keep good statements. If unclear, rewrite by filling missing behavior from
hidden tests without leaking the solution. Otherwise drop.

Input: reference ``out/instances.jsonl``.
Resume: skip if already in ``curate/out/<id>.json`` or ``curate/out/drop.json``.

- **keep / rewrite** → label ``task_type`` / ``difficulty`` (with F2P) →
  write ``curate/out/<id>.json``
- **LLM drop** → append ``curate/out/drop.json`` (no task json)
- Then **sync** copy → ``eval/data/langbridge-bench/specs/``:
  skip if specs already has the file; skip if id is in human ``eval/data/langbridge-bench/drop/drop.json``
- Prune ``docker-images/`` + tags to match **eval specs**

```bash
uv run python eval/data/data-pipeline/curate/curate.py
uv run python eval/data/data-pipeline/curate/curate.py --limit 5
```
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PIPELINE = Path(__file__).resolve().parents[1]
_CURATE = Path(__file__).resolve().parent
for _path in (_PIPELINE, _CURATE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from _lib import paths  # noqa: E402
from _lib.io import load_drop_entries, load_jsonl, save_drop_file, write_json  # noqa: E402
from _lib.spec import (  # noqa: E402
    curate_out_ids,
    eval_spec_ids,
    instance_to_task,
    prune_docker_to_eval_specs,
    save_task,
    sync_curate_out_to_specs,
)
from classify import apply_classification, classify_instance  # noqa: E402
from _lib.prompt_load import load_prompt  # noqa: E402

CURATE_SYSTEM = load_prompt("curate").CURATE_SYSTEM

_GITHUB_ISSUE_PR_URL = re.compile(
    r"https?://(?:www\.)?github\.com/[^/\s]+/[^/\s]+/(?:issues|pull)/\d+"
    r"(?:/[^\s)\]>\"']*)?",
    re.IGNORECASE,
)
_JIRA_BROWSE_URL = re.compile(
    r"https?://[^\s)\]>\"']+/browse/[A-Z][A-Z0-9]+-\d+",
    re.IGNORECASE,
)
_RELATED_ISSUE_LINE = re.compile(
    r"(?im)^[ \t]*(?:possibly\s+)?related(?:\s+to)?\s*:?\s*#\d+\s*$"
)
_PROSE_ISSUE_REF = re.compile(
    r"(?i)\b(?:fixes|closes?|resolves?|see(?:\s+also)?|related(?:\s+to)?)\s*:?\s*#\d+\b"
)
_BARE_ISSUE_NUM = re.compile(r"(?<![/\w])#(\d{2,})\b")
_JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]+)-(\d+)\b")
_JIRA_DENY_PREFIX = frozenset(
    {
        "ISO",
        "SHA",
        "UTF",
        "RFC",
        "HTTP",
        "HTML",
        "ASCII",
        "ATOM",
        "UUID",
        "CVE",
        "PYTHON",
        "PEP",
    }
)
_MULTI_BLANK = re.compile(r"\n{3,}")

# Hidden-test context for salvage rewrites (keep bounded for the judge prompt).
_HIDDEN_TEST_PATCH_CHARS = 12_000
_HIDDEN_F2P_NAME_LIMIT = 30


def strip_tracker_refs(text: str) -> str:
    """Remove GitHub issue/PR URLs, issue ``#N`` refs, and Jira URLs/keys."""
    if not text:
        return text
    out = text
    out = _GITHUB_ISSUE_PR_URL.sub("", out)
    out = _JIRA_BROWSE_URL.sub("", out)
    out = _RELATED_ISSUE_LINE.sub("", out)
    out = _PROSE_ISSUE_REF.sub("", out)
    out = _BARE_ISSUE_NUM.sub("", out)

    def _jira_repl(match: re.Match[str]) -> str:
        prefix = match.group(1)
        if prefix.upper() in _JIRA_DENY_PREFIX or prefix.upper().startswith("SHA"):
            return match.group(0)
        if len(match.group(2)) < 2:
            return match.group(0)
        return ""

    out = _JIRA_KEY.sub(_jira_repl, out)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = _MULTI_BLANK.sub("\n\n", out)
    return out.strip() + ("\n" if text.endswith("\n") else "")


def _task_id(inst: dict) -> str:
    return str(inst.get("task_id") or inst["instance_id"])


def _load_pipeline_drop() -> list[dict]:
    return load_drop_entries(paths.DEFAULT_CURATE_DROP)


def _save_pipeline_drop(dropped: list[dict]) -> None:
    save_drop_file(
        paths.DEFAULT_CURATE_DROP,
        dropped,
        description=(
            "LLM/pipeline curate drops. Code reads this for resume. "
            "Human error-analysis drops go in eval/data/langbridge-bench/drop/ instead. "
            "Not synced to eval/data/langbridge-bench/specs/."
        ),
    )


def _parse_decision(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in model reply: {raw[:200]!r}")
    data = json.loads(raw[start : end + 1])
    action = str(data.get("action") or "").strip().lower()
    if action not in {"keep", "rewrite", "drop"}:
        raise ValueError(f"invalid action {action!r}")
    reason = str(data.get("reason") or "").strip()
    statement = data.get("problem_statement")
    if action == "rewrite":
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("rewrite missing problem_statement")
        statement = statement.strip() + "\n"
    else:
        statement = None
    return {"action": action, "reason": reason, "problem_statement": statement}


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n…[truncated]…\n"


def _user_payload(task: dict) -> str:
    """Judge payload: statement + hidden-test context for salvage rewrites."""
    f2p = list(task.get("fail_to_pass") or task.get("FAIL_TO_PASS") or [])
    test_patch = str(task.get("test_patch") or "")
    return json.dumps(
        {
            "task_id": task.get("task_id"),
            "repo": task.get("repo"),
            "problem_statement": task.get("problem_statement") or "",
            "fail_to_pass_names": f2p[:_HIDDEN_F2P_NAME_LIMIT],
            "test_patch": _clip(test_patch, _HIDDEN_TEST_PATCH_CHARS),
            "notes": (
                "test_patch and fail_to_pass_names are HIDDEN from the eval agent. "
                "Use them only to decide drop vs salvage-rewrite, and to fill missing "
                "behavior/scenario details. Never copy solution-shaped content into "
                "a rewritten problem_statement."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


def judge_task(task: dict, *, api_key: str, model: str) -> dict:
    from langbridge_code.llm.client import create_model_response
    from langbridge_code.llm.parse import extract_output_text

    messages = [
        {"role": "system", "content": CURATE_SYSTEM},
        {"role": "user", "content": _user_payload(task)},
    ]
    response = create_model_response(
        api_key,
        model,
        messages,
        label="curate",
    )
    text = extract_output_text(response.get("output", []) or [])
    return _parse_decision(text or "")


def curate(*, limit: int = 0) -> dict:
    from langbridge_code.settings import DEFAULT_MODEL, load_api_key

    instances = load_jsonl(paths.DEFAULT_REFERENCE_JSONL)
    drop_entries = _load_pipeline_drop()
    drop_ids = {e["task_id"] for e in drop_entries}
    out_ids = curate_out_ids()

    rewritten: list[str] = []
    dropped: list[str] = []
    kept_llm: list[str] = []
    errors: list[dict] = []
    attempted = 0
    skipped_kept = 0
    skipped_drop = 0
    sync_info: dict[str, list[str]] = {
        "copied": [],
        "skipped_exists": [],
        "skipped_human_drop": [],
    }

    api_key = ""
    model_name = DEFAULT_MODEL
    if instances:
        api_key = load_api_key()
        if not api_key:
            raise RuntimeError("no API key; set provider key")

    paths.CURATE_OUT.mkdir(parents=True, exist_ok=True)

    for index, inst in enumerate(instances, start=1):
        task_id = _task_id(inst)
        print(f"\n[{index}/{len(instances)}] {task_id}")

        if task_id in drop_ids:
            print("  skip (already in curate/out/drop.json)")
            skipped_drop += 1
            continue

        if task_id in out_ids:
            print("  skip (already in curate/out)")
            skipped_kept += 1
            continue

        if limit and attempted >= limit:
            print(f"\n[limit] reached {limit} new attempt(s); stopping.")
            break

        attempted += 1
        task = instance_to_task(inst)

        try:
            decision = judge_task(task, api_key=api_key, model=model_name)
        except Exception as err:  # noqa: BLE001
            errors.append({"task_id": task_id, "error": str(err)})
            print(f"  error {task_id}: {err}")
            continue

        action = decision["action"]
        reason = decision["reason"]

        if action == "drop":
            drop_entries.append(
                {"task_id": task_id, "reason": reason or "llm_drop", "stage": "curate"}
            )
            drop_ids.add(task_id)
            dropped.append(task_id)
            print(f"  drop {task_id}: {reason}")
            continue

        original = task.get("problem_statement") or ""
        if action == "rewrite":
            task["problem_statement"] = strip_tracker_refs(decision["problem_statement"])
            task["problem_statement_source"] = "rewritten"
            task["rewrite_reason"] = reason or "llm_rewrite"
            rewritten.append(task_id)
            print(f"  rewrite {task_id}: {reason}")
        else:
            cleaned = strip_tracker_refs(original)
            task["problem_statement"] = cleaned
            if cleaned != original:
                task.setdefault("problem_statement_source", "sanitized")
            task.pop("rewrite_reason", None)
            kept_llm.append(task_id)
            print(f"  keep {task_id}: {reason}")

        classification = None
        try:
            classification = classify_instance(
                task, api_key=api_key, model=model_name
            )
            print(
                f"  classify: {classification.get('task_type')}/"
                f"{classification.get('difficulty')}"
            )
        except Exception as err:  # noqa: BLE001
            print(f"  ! classify failed: {err}", file=sys.stderr)
            classification = {
                "task_type": "unknown",
                "difficulty": "unknown",
                "task_type_reason": f"classify failed: {err}",
                "difficulty_reason": f"classify failed: {err}",
            }
        apply_classification(task, classification)

        task["status"] = "ok"
        save_task(task)
        out_ids.add(task_id)

    _save_pipeline_drop(drop_entries)
    sync_info = sync_curate_out_to_specs()
    for task_id in sync_info["copied"]:
        print(f"  sync → specs/{task_id}.json")
    for task_id in sync_info["skipped_human_drop"]:
        print(f"  sync skip (human drop): {task_id}")
    for task_id in sync_info["skipped_exists"]:
        print(f"  sync skip (specs exists): {task_id}")
    for orphan in prune_docker_to_eval_specs(remove_image_tags=True):
        print(f"  prune docker-images/{orphan} (+ image tag)")

    summary = {
        "curate_out": len(curate_out_ids()),
        "eval_specs": len(eval_spec_ids()),
        "pipeline_dropped": len(drop_ids),
        "llm_kept": kept_llm,
        "llm_rewritten": rewritten,
        "llm_dropped": dropped,
        "skipped_kept": skipped_kept,
        "skipped_drop": skipped_drop,
        "attempted": attempted,
        "sync": sync_info,
        "errors": errors,
        "curate_out_dir": str(paths.CURATE_OUT),
        "specs_dir": str(paths.SPECS_DIR),
        "pipeline_drop": str(paths.DEFAULT_CURATE_DROP),
    }
    write_json(paths.CURATE_OUT / "_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="stop after this many new curate attempts (prior out/drop skips do not count)",
    )
    args = parser.parse_args()

    summary = curate(limit=args.limit)
    print(json.dumps(summary, indent=2))
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
