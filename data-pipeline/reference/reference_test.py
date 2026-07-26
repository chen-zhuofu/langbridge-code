"""Stage 3 — reference F2P/P2P inside each task Docker image.

Input: env ``out/instances.jsonl``.
Resume: skip if already in **this** stage's ``out/`` or ``drop.json``.

Success → ``reference/out/instances.jsonl`` (with FAIL_TO_PASS / PASS_TO_PASS).
Failure → append ``reference/out/drop.json`` only (no deletes).

Does **not** write ``data/eval/specs/`` (curate owns that).
Sets provisional ``hard`` from F2P≥2; curate overwrites with LLM ``difficulty``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_PIPELINE = Path(__file__).resolve().parents[1]
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from _lib import paths  # noqa: E402
from _lib.docker_util import docker, image_exists  # noqa: E402
from _lib.io import load_drop_entries, load_jsonl, save_drop_file, write_jsonl  # noqa: E402
from _lib.spec import instance_to_task  # noqa: E402


def _task_id(inst: dict) -> str:
    return str(inst.get("task_id") or inst["instance_id"])


_DOCKER_REF_SCRIPT = r"""
set -euo pipefail
cd /work/repo
python3 <<'PY'
import json, re, subprocess, sys
from pathlib import Path

task = json.loads(Path("/opt/lb/task.json").read_text())
test_patch = task["test_patch"]
code_patch = task.get("gold_code_patch") or task.get("patch") or ""
test_files = task.get("test_files") or []
timeout = int(__import__("os").environ.get("LB_REF_TIMEOUT", "600"))
PYTEST_LINE_RE = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR)\b")
py = Path(".refvenv/bin/python")

def apply(patch: str) -> None:
    r = subprocess.run(
        ["git", "apply", "--whitespace=nowarn"],
        input=patch, text=True, capture_output=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"git apply failed: {r.stderr}")

def run_pytest(files):
    if not files:
        return {}
    args = [str(py), "-m", "pytest", "-v", "--no-header",
            "-p", "no:cacheprovider", "-p", "pytester",
            "-o", "addopts=", "-o", "minversion=0", *files]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    out = {}
    for line in (r.stdout + "\n" + r.stderr).splitlines():
        m = PYTEST_LINE_RE.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out

subprocess.check_call(["git", "reset", "--hard", "HEAD"])
subprocess.check_call(["git", "clean", "-fdq", "-e", ".refvenv"])
apply(test_patch)
pre = run_pytest(test_files)
apply(code_patch)
post = run_pytest(test_files)
f2p = sorted(t for t, r in post.items() if r == "PASSED" and pre.get(t) in ("FAILED", "ERROR"))
p2p = sorted(t for t, r in post.items() if r == "PASSED" and pre.get(t) == "PASSED")
print(json.dumps({"FAIL_TO_PASS": f2p, "PASS_TO_PASS": p2p, "n_tests_seen": len(post)}))
PY
"""


def reference_docker(instance, timeout: int) -> dict:
    task_id = instance.get("task_id") or instance["instance_id"]
    tag = paths.task_image(task_id)
    if not image_exists(tag):
        return {"error": f"missing image {tag}", "applies_test_patch": False, "applies_patch": False}

    task = instance_to_task(instance)
    with tempfile.TemporaryDirectory(prefix="lb-ref-") as tmp:
        local_task = Path(tmp) / "task.json"
        local_task.write_text(json.dumps(task), encoding="utf-8")
        result = docker(
            [
                "run",
                "--rm",
                "-e",
                f"LB_REF_TIMEOUT={timeout}",
                "-v",
                f"{local_task}:/opt/lb/task.json:ro",
                tag,
                "bash",
                "-lc",
                _DOCKER_REF_SCRIPT,
            ],
            timeout=timeout * 2 + 120,
        )
    if result.returncode != 0:
        return {
            "applies_test_patch": False,
            "applies_patch": False,
            "error": (result.stderr or result.stdout or "docker ref failed")[-800:],
        }
    payload = None
    for line in reversed((result.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if not payload:
        return {
            "applies_test_patch": False,
            "applies_patch": False,
            "error": f"no JSON from container: {(result.stdout or '')[-500:]}",
        }
    return {
        "applies_test_patch": True,
        "applies_patch": True,
        "FAIL_TO_PASS": payload.get("FAIL_TO_PASS", []),
        "PASS_TO_PASS": payload.get("PASS_TO_PASS", []),
        "n_tests_seen": payload.get("n_tests_seen", 0),
    }


_REF_TIMEOUT = 600


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="stop after this many new reference attempts (prior kept/drops do not count)",
    )
    args = parser.parse_args()

    instances_path = paths.DEFAULT_ENV_JSONL
    out_path = paths.DEFAULT_REFERENCE_JSONL
    drop_path = paths.DEFAULT_REFERENCE_DROP

    instances = load_jsonl(instances_path)

    kept_by_id: dict[str, dict] = {}
    if out_path.exists():
        for row in load_jsonl(out_path):
            kept_by_id[_task_id(row)] = row

    dropped = load_drop_entries(drop_path)
    drop_ids = {e["task_id"] for e in dropped}

    attempted = 0
    skipped_drop = 0
    skipped_kept = 0

    def record_drop(task_id: str, reason: str) -> None:
        print(f"  DROP: {reason}")
        dropped.append({"task_id": task_id, "reason": reason, "stage": "reference"})
        drop_ids.add(task_id)
        kept_by_id.pop(task_id, None)

    for index, instance in enumerate(instances, start=1):
        task_id = _task_id(instance)
        instance.setdefault("instance_id", task_id)
        instance.setdefault("task_id", task_id)
        print(f"\n[{index}/{len(instances)}] {task_id}")

        if task_id in drop_ids:
            print("  skip (already in reference drop.json)")
            skipped_drop += 1
            kept_by_id.pop(task_id, None)
            continue

        if task_id in kept_by_id:
            print("  skip (already in reference out)")
            skipped_kept += 1
            continue

        if args.limit and attempted >= args.limit:
            print(f"\n[limit] reached {args.limit} new attempt(s); stopping.")
            break

        attempted += 1
        try:
            if not image_exists(paths.task_image(task_id)):
                record_drop(task_id, f"missing image {paths.task_image(task_id)}")
                continue
            status = reference_docker(instance, _REF_TIMEOUT)
        except subprocess.TimeoutExpired:
            record_drop(task_id, "timed out")
            continue
        except subprocess.CalledProcessError as failure:
            record_drop(task_id, f"setup failed: {failure.stderr or failure}")
            continue

        print(
            f"  applies: test_patch={status.get('applies_test_patch')} "
            f"patch={status.get('applies_patch')}"
        )
        if not (status.get("applies_test_patch") and status.get("applies_patch")):
            record_drop(task_id, status.get("error") or "patch apply failed")
            continue

        f2p = status.get("FAIL_TO_PASS", [])
        p2p = status.get("PASS_TO_PASS", [])
        print(
            f"  FAIL_TO_PASS={len(f2p)} PASS_TO_PASS={len(p2p)} "
            f"(seen={status.get('n_tests_seen')})"
        )
        if not f2p:
            record_drop(task_id, "no FAIL_TO_PASS")
            continue
        instance["FAIL_TO_PASS"] = f2p
        instance["PASS_TO_PASS"] = p2p
        instance["fail_to_pass"] = f2p
        instance["pass_to_pass"] = p2p
        instance["status"] = "ok"
        instance["hard"] = len(f2p) >= 2

        kept_by_id[task_id] = instance

    kept = list(kept_by_id.values())
    write_jsonl(out_path, kept)
    save_drop_file(
        drop_path,
        dropped,
        description=(
            "Tasks dropped at reference (no FAIL_TO_PASS, patch apply failure, "
            "missing image, timeout, …)."
        ),
    )
    print(
        f"\nKept {len(kept)} (attempted {attempted}, skip-drop {skipped_drop}, "
        f"skip-kept {skipped_kept}) -> {out_path}"
    )
    print(f"Dropped {len(dropped)} -> {drop_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
