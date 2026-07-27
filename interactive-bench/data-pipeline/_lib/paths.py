"""Paths for the interactive (SWE-Chat) bench.

Pipeline stage outs live under ``interactive-bench/data-pipeline/``.
Eval-facing specs / docker-images / drops live under ``interactive-bench/data/``.
"""
from __future__ import annotations

from pathlib import Path

# interactive-bench/data-pipeline/_lib/paths.py
PIPELINE_ROOT = Path(__file__).resolve().parents[1]
BENCH_ROOT = PIPELINE_ROOT.parent
REPO_ROOT = BENCH_ROOT.parent

COLLECT_OUT = PIPELINE_ROOT / "collect" / "out"
RESOLVE_OUT = PIPELINE_ROOT / "resolve" / "out"
ENRICH_OUT = PIPELINE_ROOT / "enrich" / "out"
INTENT_OUT = PIPELINE_ROOT / "intent" / "out"
ENV_OUT = PIPELINE_ROOT / "env" / "out"
REFERENCE_OUT = PIPELINE_ROOT / "reference" / "out"
CURATE_OUT = PIPELINE_ROOT / "curate" / "out"
HARNESS_OUT = BENCH_ROOT / "harness" / "out"
EVAL_OUT = BENCH_ROOT / "eval" / "out"

DATA_DIR = BENCH_ROOT / "data"
SPECS_DIR = DATA_DIR / "specs"
DOCKER_IMAGES_DIR = DATA_DIR / "docker-images"
DROP_DIR = DATA_DIR / "drop"

DEFAULT_COLLECT_JSONL = COLLECT_OUT / "sessions.jsonl"
DEFAULT_COLLECT_DROP = COLLECT_OUT / "drop.json"
DEFAULT_RESOLVE_JSONL = RESOLVE_OUT / "instances.jsonl"
DEFAULT_RESOLVE_DROP = RESOLVE_OUT / "drop.json"
DEFAULT_ENRICH_JSONL = ENRICH_OUT / "instances.jsonl"
DEFAULT_ENRICH_DROP = ENRICH_OUT / "drop.json"
DEFAULT_INTENT_JSONL = INTENT_OUT / "instances.jsonl"
DEFAULT_INTENT_DROP = INTENT_OUT / "drop.json"
DEFAULT_ENV_JSONL = ENV_OUT / "instances.jsonl"
DEFAULT_ENV_DROP = ENV_OUT / "drop.json"
DEFAULT_REFERENCE_JSONL = REFERENCE_OUT / "instances.jsonl"
DEFAULT_REFERENCE_DROP = REFERENCE_OUT / "drop.json"
DEFAULT_CURATE_JSONL = CURATE_OUT / "instances.jsonl"
DEFAULT_CURATE_DROP = CURATE_OUT / "drop.json"
DEFAULT_HUMAN_DROP = DROP_DIR / "drop.json"

IMAGE_PREFIX = "lb-interactive"


def task_image(task_id: str) -> str:
    return f"{IMAGE_PREFIX}:{task_id}"


def task_dir(task_id: str) -> Path:
    return DOCKER_IMAGES_DIR / task_id


def spec_path(task_id: str) -> Path:
    return SPECS_DIR / f"{task_id}.json"
