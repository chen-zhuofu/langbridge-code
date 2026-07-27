"""Reference stage admission logic with mocked docker."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from reference.reference_test import reference_docker  # noqa: E402


def test_reference_docker_missing_image():
    with patch("reference.reference_test.image_exists", return_value=False):
        out = reference_docker(
            {
                "task_id": "t1",
                "docker_image": "lb-interactive:t1",
                "test_patch": "x",
                "gold_code_patch": "y",
                "repo": "a/b",
                "base_commit": "b" * 40,
                "gold_commit": "g" * 40,
                "session_id": "s",
            }
        )
    assert "missing image" in out["error"]


def test_reference_docker_parses_f2p_json():
    payload = {
        "FAIL_TO_PASS": ["tests/t.py::test_a"],
        "PASS_TO_PASS": ["tests/t.py::test_b"],
        "n_tests_seen": 2,
    }
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = f"noise\n{json.dumps(payload)}\n"
    proc.stderr = ""

    with patch("reference.reference_test.image_exists", return_value=True), patch(
        "reference.reference_test.docker", return_value=proc
    ):
        out = reference_docker(
            {
                "task_id": "t1",
                "docker_image": "lb-interactive:t1",
                "test_patch": "tp",
                "gold_code_patch": "cp",
                "test_files": ["tests/t.py"],
                "repo": "a/b",
                "base_commit": "b" * 40,
                "gold_commit": "g" * 40,
                "session_id": "s",
                "instruction": "x",
                "intents": [],
            }
        )
    assert out["FAIL_TO_PASS"] == ["tests/t.py::test_a"]
    assert out["PASS_TO_PASS"] == ["tests/t.py::test_b"]
    assert out["n_tests_seen"] == 2
