import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

# run_eval_docker.py needs the optional "eval" dependency group (uv sync --group eval).
pytest.importorskip("datasets")
pytest.importorskip("swebench")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "evals" / "swe-bench" / "run_eval_docker.py"
SPEC = importlib.util.spec_from_file_location("run_eval_docker", MODULE_PATH)
docker_eval = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(docker_eval)


def test_export_container_artifacts_skips_missing_paths(tmp_path):
    with patch.object(docker_eval, "container_exec", return_value=type("R", (), {"returncode": 1})()):
        exports = docker_eval.export_container_artifacts("container", tmp_path)
    assert exports == {}


def test_export_container_artifacts_copies_existing_paths(tmp_path):
    def fake_exec(container, command):
        path = command.removeprefix("test -e ")
        exists = path in {
            docker_eval.CONTAINER_LANGBRIDGE_ARTIFACTS,
            docker_eval.CONTAINER_ARTIFACTS,
        }
        return type("R", (), {"returncode": 0 if exists else 1})()

    def fake_docker(args, **kwargs):
        container_path = args[1].split(":", 1)[1]
        host_dir = Path(args[2])
        dest = host_dir / Path(container_path).name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "marker.txt").write_text(container_path, encoding="utf-8")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch.object(docker_eval, "container_exec", side_effect=fake_exec):
        with patch.object(docker_eval, "docker", side_effect=fake_docker):
            exports = docker_eval.export_container_artifacts("container", tmp_path)

    assert set(exports) == {"langbridge_artifacts", "lb_artifacts"}
    assert (tmp_path / exports["langbridge_artifacts"] / "marker.txt").read_text() == (
        docker_eval.CONTAINER_LANGBRIDGE_ARTIFACTS
    )
