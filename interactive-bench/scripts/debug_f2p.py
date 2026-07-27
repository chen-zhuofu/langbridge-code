#!/usr/bin/env python3
"""Debug F2P inside a built lb-interactive image."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
PIPELINE = BENCH / "data-pipeline"
sys.path.insert(0, str(PIPELINE))

from _lib.docker_util import docker  # noqa: E402
from _lib.spec import build_interactive_spec  # noqa: E402

DEBUG_SCRIPT = r"""
set -euo pipefail
cd /work/repo
python3 <<'PY'
import json, os, re, subprocess
from pathlib import Path
task = json.loads(Path("/opt/lb/task.json").read_text())
test_files = [f for f in (task.get("test_files") or []) if str(f).endswith(".py")]
py = ".refvenv/bin/python"
subprocess.check_call(["git", "reset", "--hard", "HEAD"])
subprocess.check_call(["git", "clean", "-fdq", "-e", ".refvenv"])
r = subprocess.run(
    ["git", "apply", "--whitespace=nowarn"],
    input=task["test_patch"],
    text=True,
    capture_output=True,
)
print("apply_test", r.returncode, (r.stderr or "")[:400])
env = os.environ.copy()
env["PYTHONPATH"] = "/work/repo/backend:/work/repo"
args = [
    py, "-m", "pytest", "-v", "--no-header",
    "-p", "no:cacheprovider", "-o", "addopts=",
    *test_files,
]
r = subprocess.run(args, capture_output=True, text=True, timeout=180, env=env)
print("RC", r.returncode)
print("STDOUT:\n", (r.stdout or "")[-2500:])
print("STDERR:\n", (r.stderr or "")[-1500:])
PY
"""


def main() -> int:
    task_id = sys.argv[1] if len(sys.argv) > 1 else ""
    rows = [
        json.loads(line)
        for line in (PIPELINE / "env/out/instances.jsonl").read_text().splitlines()
        if line.strip()
    ]
    row = next((r for r in rows if task_id in r["task_id"]), None)
    if not row:
        print("task not found", task_id)
        return 1
    task = build_interactive_spec(row)
    task["test_files"] = row.get("test_files") or []
    with tempfile.TemporaryDirectory(prefix="lb-dbg-") as tmp:
        local = Path(tmp) / "task.json"
        local.write_text(json.dumps(task), encoding="utf-8")
        result = docker(
            [
                "run",
                "--rm",
                "-v",
                f"{local}:/opt/lb/task.json:ro",
                row["docker_image"],
                "bash",
                "-lc",
                DEBUG_SCRIPT,
            ],
            timeout=240,
        )
    print(result.stdout or "")
    if result.stderr:
        print("DOCKER_ERR", result.stderr[-800:])
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
