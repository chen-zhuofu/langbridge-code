"""Instance ↔ eval-spec helpers.

Curate writes ``curate/out/``; sync copies into ``data/eval/specs/``.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from . import paths
from .io import load_json, write_json

TEST_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)

_NON_SPEC_NAMES = frozenset({"drop.json", "_summary.json"})


def test_files_in_patch(test_patch: str) -> list[str]:
    return TEST_FILE_RE.findall(test_patch or "")


def instance_metadata(inst: dict) -> dict:
    """Human-facing provenance; eval runners ignore this block."""
    meta: dict[str, Any] = {}
    if inst.get("_pr_url") or inst.get("pr_url"):
        meta["pr_url"] = inst.get("_pr_url") or inst.get("pr_url")
    linked = inst.get("_linked_issues")
    if linked is None and inst.get("metadata"):
        linked = inst["metadata"].get("linked_issues")
    if linked is not None:
        meta["linked_issues"] = list(linked)
    if inst.get("_github_issue_urls") or (inst.get("metadata") or {}).get("github_issue_urls"):
        meta["github_issue_urls"] = list(
            inst.get("_github_issue_urls")
            or inst.get("metadata", {}).get("github_issue_urls")
            or []
        )
    if inst.get("_jira_url") or (inst.get("metadata") or {}).get("jira_url"):
        meta["jira_url"] = inst.get("_jira_url") or inst["metadata"]["jira_url"]
    if inst.get("_jira_key") or (inst.get("metadata") or {}).get("jira_key"):
        meta["jira_key"] = inst.get("_jira_key") or inst["metadata"]["jira_key"]
    if inst.get("_num_files") is not None:
        meta["num_files"] = inst["_num_files"]
    elif (inst.get("metadata") or {}).get("num_files") is not None:
        meta["num_files"] = inst["metadata"]["num_files"]
    if inst.get("created_at"):
        meta["created_at"] = inst["created_at"]
    elif (inst.get("metadata") or {}).get("created_at"):
        meta["created_at"] = inst["metadata"]["created_at"]
    for key, value in (inst.get("metadata") or {}).items():
        meta.setdefault(key, value)
    meta.pop("classify_reason", None)
    return meta


def _difficulty_of(inst: dict) -> str | None:
    value = inst.get("difficulty")
    if value is None and inst.get("metadata"):
        value = inst["metadata"].get("difficulty")
    if isinstance(value, str) and value.lower() in {
        "easy",
        "medium",
        "hard",
        "unknown",
    }:
        return value.lower()
    return None


def instance_to_task(inst: dict) -> dict:
    """Convert a collect/env/reference instance into an eval spec dict."""
    f2p = inst.get("FAIL_TO_PASS") or inst.get("fail_to_pass") or []
    p2p = inst.get("PASS_TO_PASS") or inst.get("pass_to_pass") or []
    test_patch = inst.get("test_patch", "")
    task_id = inst.get("task_id") or inst["instance_id"]
    difficulty = _difficulty_of(inst)
    if difficulty is not None:
        hard = difficulty == "hard"
    else:
        hard = bool(inst.get("hard")) or len(f2p) >= 2
    task = {
        "task_id": task_id,
        "status": inst.get("status") or ("ok" if f2p else "pending"),
        "repo": inst["repo"],
        "base_commit": inst["base_commit"],
        "problem_statement": inst.get("problem_statement", ""),
        "test_files": inst.get("test_files") or test_files_in_patch(test_patch),
        "test_patch": test_patch,
        "gold_code_patch": inst.get("gold_code_patch") or inst.get("patch", ""),
        "fail_to_pass": list(f2p),
        "pass_to_pass": list(p2p),
        "hard": hard,
    }
    if difficulty is not None:
        task["difficulty"] = difficulty
    meta = instance_metadata(inst)
    if meta:
        task["metadata"] = meta
    for key in (
        "task_type",
        "task_type_reason",
        "difficulty",
        "difficulty_reason",
        "problem_statement_source",
        "rewrite_reason",
        "required_tool_calls",
        "docker_image",
    ):
        if key in inst and key not in task:
            task[key] = inst[key]
    # Legacy alias from older pipeline stages / specs.
    if "task_type" not in task and inst.get("task_kind"):
        task["task_type"] = inst["task_kind"]
    if "docker_image" not in task:
        task["docker_image"] = paths.task_image(task_id)
    return task


def _task_json_ids(directory: Path) -> set[str]:
    if not directory.exists():
        return set()
    return {
        p.stem
        for p in directory.glob("*.json")
        if (p.is_file() or p.is_symlink())
        and not p.name.startswith("_")
        and p.name not in _NON_SPEC_NAMES
    }


def curate_out_ids() -> set[str]:
    """Ids present in curate pipeline out."""
    return _task_json_ids(paths.CURATE_OUT)


def eval_spec_ids() -> set[str]:
    """Ids present in eval-facing ``data/eval/specs``."""
    return _task_json_ids(paths.SPECS_DIR)


def human_dropped_ids() -> set[str]:
    """Ids listed in human ``data/eval/drop/drop.json`` (for sync skip only)."""
    from .io import dropped_task_ids_from_json

    return dropped_task_ids_from_json(paths.DEFAULT_HUMAN_DROP)


def load_task(task_id: str) -> dict | None:
    """Load eval spec from ``data/eval/specs`` (preferred) or curate out."""
    for directory in (paths.SPECS_DIR, paths.CURATE_OUT):
        path = directory / f"{task_id}.json"
        if path.exists():
            return load_json(path)
    return None


def save_task(task: dict, *, sync_spec: bool = True) -> Path:
    """Persist task under ``curate/out/`` only (sync to specs is separate)."""
    del sync_spec
    task_id = task["task_id"]
    task.setdefault("docker_image", paths.task_image(task_id))
    paths.CURATE_OUT.mkdir(parents=True, exist_ok=True)
    dest = paths.CURATE_OUT / f"{task_id}.json"
    write_json(dest, task)
    return dest


def sync_curate_out_to_specs() -> dict[str, list[str]]:
    """Copy ``curate/out/*.json`` → ``data/eval/specs/``.

    Skip if already present in specs, or listed in human drop.json.
    Does not overwrite existing specs files.
    """
    paths.SPECS_DIR.mkdir(parents=True, exist_ok=True)
    if paths.SPECS_DIR.is_symlink():
        raise RuntimeError(
            f"{paths.SPECS_DIR} is a symlink; expected a real directory. "
            "Replace the symlink with a normal folder first."
        )

    human_drop = human_dropped_ids()
    existing = eval_spec_ids()
    copied: list[str] = []
    skipped_exists: list[str] = []
    skipped_human_drop: list[str] = []

    for src in sorted(paths.CURATE_OUT.glob("*.json")):
        if src.name.startswith("_") or src.name in _NON_SPEC_NAMES:
            continue
        task_id = src.stem
        if task_id in human_drop:
            skipped_human_drop.append(task_id)
            continue
        dest = paths.SPECS_DIR / src.name
        if dest.exists() or task_id in existing:
            skipped_exists.append(task_id)
            continue
        shutil.copy2(src, dest)
        copied.append(task_id)

    return {
        "copied": copied,
        "skipped_exists": skipped_exists,
        "skipped_human_drop": skipped_human_drop,
    }


def prune_docker_to_eval_specs(*, remove_image_tags: bool = True) -> list[str]:
    """Keep docker-images + tags for eval specs and mid-pipeline work.

    Do not delete images that still have pending env→reference→curate work;
    otherwise a curate pass would wipe a freshly built env image before
    reference runs.
    """
    from .docker_util import remove_image
    from .io import dropped_task_ids_from_json, existing_task_ids_from_jsonl

    env_out = existing_task_ids_from_jsonl(paths.DEFAULT_ENV_JSONL)
    ref_out = existing_task_ids_from_jsonl(paths.DEFAULT_REFERENCE_JSONL)
    ref_drop = dropped_task_ids_from_json(paths.DEFAULT_REFERENCE_DROP)
    curate_drop = dropped_task_ids_from_json(paths.DEFAULT_CURATE_DROP)
    pending_reference = env_out - ref_out - ref_drop
    keep = (
        eval_spec_ids()
        | curate_out_ids()
        | ref_out
        | pending_reference
    ) - human_dropped_ids() - curate_drop

    removed: list[str] = []
    if paths.DOCKER_IMAGES_DIR.exists():
        for child in list(paths.DOCKER_IMAGES_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name in keep:
                continue
            shutil.rmtree(child)
            if remove_image_tags:
                remove_image(paths.task_image(child.name))
            removed.append(child.name)
    return removed


# Back-compat aliases used by older call sites.
active_spec_ids = curate_out_ids
prune_docker_to_active_specs = prune_docker_to_eval_specs
