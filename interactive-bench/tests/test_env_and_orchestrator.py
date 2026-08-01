"""env Dockerfile prep + run_pipeline --only wiring."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def test_prepare_dockerfile(tmp_path, monkeypatch):
    from _lib import paths
    from env import build_env

    monkeypatch.setattr(paths, "DOCKER_IMAGES_DIR", tmp_path)
    monkeypatch.setattr(paths, "task_dir", lambda tid: tmp_path / tid)

    tdir = build_env.prepare_dockerfile(
        {
            "task_id": "demo-task",
            "repo": "org/repo",
            "base_commit": "abc123",
        }
    )
    dockerfile = tdir / "Dockerfile"
    assert dockerfile.exists()
    text = dockerfile.read_text(encoding="utf-8")
    assert "org/repo" in text
    assert "abc123" in text
    assert "lb-interactive" not in text  # tag is separate; Dockerfile is FROM base


def test_run_pipeline_only_requires_data_dir(monkeypatch):
    import run_pipeline as rp

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--only", "collect", "--limit", "1"],
    )
    assert rp.main() == 2


def test_run_pipeline_only_curate(monkeypatch):
    import run_pipeline as rp

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--only", "curate", "--limit", "1"],
    )
    with patch("run_pipeline.run_stage", return_value=0) as mocked:
        assert rp.main() == 0
        mocked.assert_called_once()
        assert mocked.call_args.args[0] == "curate"
