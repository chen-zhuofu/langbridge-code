"""Canonical paths for the dataset pipeline and eval artifacts.

Invariant per stage: ``own_output ∪ own_drop == previous_stage_output``
(for ids that stage has decided). Each stage only reads its own out + drop
(+ the previous stage's out as input). Never reads another stage's drop
(except curate sync reads human ``data/langbridge-bench/drop/drop.json`` to skip copy).

- Curate writes ``data-pipeline/curate/out/``
- Then syncs copies into ``data/langbridge-bench/specs/`` (real dir, not a symlink):
  skip if already in specs, skip if listed in human drop.json
- LLM curate drops: ``data-pipeline/curate/out/drop.json``
- Human drops: ``data/langbridge-bench/drop/``
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
PIPELINE_DIR = REPO_ROOT / "data-pipeline"
EVAL_DIR = DATA_DIR / "langbridge-bench"

COLLECT_DIR = PIPELINE_DIR / "collect"
ENV_DIR = PIPELINE_DIR / "env"
REFERENCE_DIR = PIPELINE_DIR / "reference"
CURATE_DIR = PIPELINE_DIR / "curate"

COLLECT_IN = COLLECT_DIR / "in"
COLLECT_OUT = COLLECT_DIR / "out"
ENV_OUT = ENV_DIR / "out"
REFERENCE_OUT = REFERENCE_DIR / "out"
CURATE_OUT = CURATE_DIR / "out"

SPECS_DIR = EVAL_DIR / "specs"
DOCKER_IMAGES_DIR = EVAL_DIR / "docker-images"
LEGACY_DIR = EVAL_DIR / "_legacy"
HUMAN_DROP_DIR = EVAL_DIR / "drop"

DEFAULT_REPOS_MD = COLLECT_IN / "repos.md"
DEFAULT_COLLECT_JSONL = COLLECT_OUT / "instances.jsonl"
DEFAULT_ENV_JSONL = ENV_OUT / "instances.jsonl"
DEFAULT_ENV_DROP = ENV_OUT / "drop.json"
DEFAULT_REFERENCE_JSONL = REFERENCE_OUT / "instances.jsonl"
DEFAULT_REFERENCE_DROP = REFERENCE_OUT / "drop.json"
DEFAULT_CURATE_DROP = CURATE_OUT / "drop.json"
DEFAULT_HUMAN_DROP = HUMAN_DROP_DIR / "drop.json"

IMAGE_PREFIX = "lb-task"


def task_image(task_id: str) -> str:
    return f"{IMAGE_PREFIX}:{task_id}"


def task_dir(task_id: str) -> Path:
    """Per-task Docker build context (Dockerfile only)."""
    return DOCKER_IMAGES_DIR / task_id
