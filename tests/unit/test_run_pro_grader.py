import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "eval" / "run_pro_grader.py"
SPEC = importlib.util.spec_from_file_location("run_pro_grader", MODULE_PATH)
grader = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(grader)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], ["--num_workers", "2"]),
        (
            ["--patch_path", "p.json", "--num_workers", "50"],
            ["--patch_path", "p.json", "--num_workers", "2"],
        ),
        (["--num_workers=4"], ["--num_workers=2"]),
        (["--num_workers", "1"], ["--num_workers", "1"]),
    ],
)
def test_clamp_grader_workers(arguments, expected):
    assert grader.clamp_grader_workers(arguments) == expected


def test_resource_safe_run_kwargs_preserves_official_options():
    original = {
        "detach": True,
        "network_mode": "none",
        "environment": {"EXISTING": "yes"},
    }

    safe = grader.resource_safe_run_kwargs(original)

    assert original["environment"] == {"EXISTING": "yes"}
    assert safe["detach"] is True
    assert safe["network_mode"] == "none"
    assert safe["platform"] == "linux/amd64"
    assert safe["nano_cpus"] == 2_000_000_000
    assert safe["environment"] == {
        "EXISTING": "yes",
        "GOFLAGS": "-p=1",
        "GOMAXPROCS": "2",
    }


def test_run_official_grader_restores_process_state(tmp_path, monkeypatch):
    script = tmp_path / "official.py"
    script.write_text(
        "import sys\n"
        "assert '--num_workers=2' in sys.argv\n",
        encoding="utf-8",
    )
    previous = list(sys.argv)
    previous_path = list(sys.path)

    class NoopPatch:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(grader, "patched_docker_run", lambda: NoopPatch())
    grader.run_official_grader(script, ["--num_workers=100"])

    assert sys.argv == previous
    assert sys.path == previous_path
