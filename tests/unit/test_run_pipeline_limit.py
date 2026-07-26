"""run_pipeline --limit means N new eval specs, not N attempts per stage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[2] / "data-pipeline"
sys.path.insert(0, str(PIPELINE))

import run_pipeline as rp  # noqa: E402
from _lib import paths  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_drop(path: Path, ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "dropped": [
                    {"task_id": tid, "reason": "test", "stage": "x"} for tid in ids
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def pipeline_tree(tmp_path, monkeypatch):
    """Point path constants at an isolated fake pipeline layout."""
    root = tmp_path
    collect = root / "collect" / "out"
    env = root / "env" / "out"
    ref = root / "reference" / "out"
    curate = root / "curate" / "out"
    specs = root / "eval" / "specs"
    for d in (collect, env, ref, curate, specs):
        d.mkdir(parents=True)

    monkeypatch.setattr(paths, "DEFAULT_COLLECT_JSONL", collect / "instances.jsonl")
    monkeypatch.setattr(paths, "DEFAULT_ENV_JSONL", env / "instances.jsonl")
    monkeypatch.setattr(paths, "DEFAULT_ENV_DROP", env / "drop.json")
    monkeypatch.setattr(paths, "DEFAULT_REFERENCE_JSONL", ref / "instances.jsonl")
    monkeypatch.setattr(paths, "DEFAULT_REFERENCE_DROP", ref / "drop.json")
    monkeypatch.setattr(paths, "DEFAULT_CURATE_DROP", curate / "drop.json")
    monkeypatch.setattr(paths, "CURATE_OUT", curate)
    monkeypatch.setattr(paths, "SPECS_DIR", specs)
    return {
        "collect": collect / "instances.jsonl",
        "env": env / "instances.jsonl",
        "env_drop": env / "drop.json",
        "ref": ref / "instances.jsonl",
        "ref_drop": ref / "drop.json",
        "curate": curate,
        "curate_drop": curate / "drop.json",
        "specs": specs,
    }


def test_next_stage_prefers_downstream_backlog(pipeline_tree):
    _write_jsonl(
        pipeline_tree["collect"],
        [{"task_id": "a"}, {"task_id": "b"}, {"task_id": "c"}],
    )
    _write_jsonl(pipeline_tree["env"], [{"task_id": "a"}, {"task_id": "b"}])
    _write_jsonl(pipeline_tree["ref"], [{"task_id": "a"}])

    assert rp.pending_for("curate") == {"a"}
    assert rp.pending_for("reference") == {"b"}
    assert rp.pending_for("env") == {"c"}
    assert rp.next_stage(list(rp.STAGES)) == "curate"

    # After curate clears, drain reference before collecting.
    (pipeline_tree["curate"] / "a.json").write_text("{}", encoding="utf-8")
    assert rp.next_stage(list(rp.STAGES)) == "reference"

    _write_jsonl(pipeline_tree["ref"], [{"task_id": "a"}, {"task_id": "b"}])
    (pipeline_tree["curate"] / "b.json").write_text("{}", encoding="utf-8")
    assert rp.next_stage(list(rp.STAGES)) == "env"

    # Env catches up on c → c is now pending reference (still prefer downstream).
    _write_jsonl(
        pipeline_tree["env"],
        [{"task_id": "a"}, {"task_id": "b"}, {"task_id": "c"}],
    )
    assert rp.next_stage(list(rp.STAGES)) == "reference"

    _write_jsonl(
        pipeline_tree["ref"],
        [{"task_id": "a"}, {"task_id": "b"}, {"task_id": "c"}],
    )
    (pipeline_tree["curate"] / "c.json").write_text("{}", encoding="utf-8")
    assert rp.next_stage(list(rp.STAGES)) == "collect"


def test_pending_skips_drops(pipeline_tree):
    _write_jsonl(pipeline_tree["collect"], [{"task_id": "x"}, {"task_id": "y"}])
    _write_jsonl(pipeline_tree["env"], [{"task_id": "x"}])
    _write_drop(pipeline_tree["ref_drop"], ["x"])
    assert rp.pending_for("reference") == set()
    assert rp.pending_for("env") == {"y"}
