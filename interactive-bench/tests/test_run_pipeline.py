"""run_pipeline pending / stage selection logic."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def test_pending_and_next_stage(tmp_path, monkeypatch):
    import run_pipeline as rp
    from _lib import paths

    def write_jsonl(path: Path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )

    def write_drop(path: Path, ids):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"dropped": [{"task_id": i, "reason": "x"} for i in ids]}),
            encoding="utf-8",
        )

    collect = tmp_path / "collect.jsonl"
    resolve = tmp_path / "resolve.jsonl"
    resolve_drop = tmp_path / "resolve_drop.json"
    enrich = tmp_path / "enrich.jsonl"
    enrich_drop = tmp_path / "enrich_drop.json"
    env = tmp_path / "env.jsonl"
    env_drop = tmp_path / "env_drop.json"
    ref = tmp_path / "ref.jsonl"
    ref_drop = tmp_path / "ref_drop.json"
    curate = tmp_path / "curate.jsonl"
    curate_drop = tmp_path / "curate_drop.json"

    write_jsonl(collect, [{"task_id": "a"}, {"task_id": "b"}, {"task_id": "c"}])
    write_jsonl(resolve, [{"task_id": "a"}])
    write_drop(resolve_drop, ["b"])
    write_jsonl(enrich, [])
    write_drop(enrich_drop, [])
    write_jsonl(env, [])
    write_drop(env_drop, [])
    write_jsonl(ref, [])
    write_drop(ref_drop, [])
    write_jsonl(curate, [])
    write_drop(curate_drop, [])

    monkeypatch.setattr(paths, "DEFAULT_COLLECT_JSONL", collect)
    monkeypatch.setattr(paths, "DEFAULT_RESOLVE_JSONL", resolve)
    monkeypatch.setattr(paths, "DEFAULT_RESOLVE_DROP", resolve_drop)
    monkeypatch.setattr(paths, "DEFAULT_ENRICH_JSONL", enrich)
    monkeypatch.setattr(paths, "DEFAULT_ENRICH_DROP", enrich_drop)
    monkeypatch.setattr(paths, "DEFAULT_ENV_JSONL", env)
    monkeypatch.setattr(paths, "DEFAULT_ENV_DROP", env_drop)
    monkeypatch.setattr(paths, "DEFAULT_REFERENCE_JSONL", ref)
    monkeypatch.setattr(paths, "DEFAULT_REFERENCE_DROP", ref_drop)
    monkeypatch.setattr(paths, "DEFAULT_CURATE_JSONL", curate)
    monkeypatch.setattr(paths, "DEFAULT_CURATE_DROP", curate_drop)

    assert rp.STAGES == (
        "collect",
        "resolve",
        "enrich",
        "env",
        "reference",
        "curate",
    )
    assert rp.pending_for("resolve") == {"c"}
    assert rp.pending_for("enrich") == {"a"}
    assert rp.pending_for("env") == set()
    # Prefer draining enrich before resolve when both pending
    assert rp.next_stage(list(rp.STAGES)) == "enrich"

    # After enrich lands, env is next — curate waits for F2P via reference.
    write_jsonl(enrich, [{"task_id": "a"}])
    assert rp.pending_for("env") == {"a"}
    assert rp.pending_for("curate") == set()
    assert rp.next_stage(list(rp.STAGES)) == "env"

    write_jsonl(env, [{"task_id": "a"}])
    write_jsonl(ref, [{"task_id": "a"}])
    assert rp.pending_for("curate") == {"a"}
    assert rp.next_stage(list(rp.STAGES)) == "curate"
