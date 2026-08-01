"""run_pipeline --limit means N new eval specs, not N attempts per stage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[2] / "eval" / "data-pipeline"
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
    specs = root / "langbridge-bench" / "specs"
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


def test_repo_progress_counts_only_current_run(pipeline_tree):
    _write_jsonl(
        pipeline_tree["collect"],
        [
            {"task_id": "old", "repo": "owner/old"},
            {"task_id": "new-pending", "repo": "owner/a"},
            {"task_id": "new-dropped", "repo": "owner/b"},
        ],
    )
    _write_drop(pipeline_tree["env_drop"], ["new-dropped"])
    (pipeline_tree["specs"] / "new-done.json").write_text(
        json.dumps({"task_id": "new-done", "repo": "owner/a"}),
        encoding="utf-8",
    )

    assert rp.repo_progress(
        before_specs=set(),
        before_collect={"old"},
    ) == {
        "owner/a": {"done": 1, "pending": 1, "failed": 0},
        "owner/b": {"done": 0, "pending": 0, "failed": 1},
    }


def test_repo_progress_marks_structural_env_mismatch_exhausted(pipeline_tree):
    _write_jsonl(
        pipeline_tree["collect"],
        [{"task_id": "node-task", "repo": "owner/node"}],
    )
    pipeline_tree["env_drop"].write_text(
        json.dumps(
            {
                "dropped": [
                    {
                        "task_id": "node-task",
                        "reason": (
                            "/work/repo does not appear to be a Python project"
                        ),
                        "stage": "env",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert rp.repo_progress(
        before_specs=set(),
        before_collect=set(),
    ) == {
        "owner/node": {
            "done": 0,
            "pending": 0,
            "failed": 1,
            "exhausted": True,
        }
    }


def test_repo_progress_exhausts_empty_repo_after_failure_budget(pipeline_tree):
    rows = [
        {"task_id": f"failed-{index}", "repo": "owner/empty"}
        for index in range(rp.MAX_FAILED_ATTEMPTS_PER_EMPTY_REPO)
    ]
    _write_jsonl(pipeline_tree["collect"], rows)
    _write_drop(
        pipeline_tree["env_drop"],
        [row["task_id"] for row in rows],
    )

    assert rp.repo_progress(
        before_specs=set(),
        before_collect=set(),
    ) == {
        "owner/empty": {
            "done": 0,
            "pending": 0,
            "failed": rp.MAX_FAILED_ATTEMPTS_PER_EMPTY_REPO,
            "exhausted": True,
        }
    }


def test_collect_exhaustion_redistributes_before_stuck(pipeline_tree, monkeypatch):
    specs: set[str] = set()
    calls = 0

    monkeypatch.setattr(rp, "eval_spec_ids", lambda: set(specs))
    monkeypatch.setattr(rp, "pending_for", lambda _stage: set())

    def fake_collect(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 0, {"owner/exhausted"}
        specs.add("new-task")
        return 0, set()

    monkeypatch.setattr(rp, "run_collect_balanced", fake_collect)

    assert rp.run_until_benches(["collect"], target=1) == 0
    assert calls == 2
