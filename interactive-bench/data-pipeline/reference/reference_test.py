"""Reference F2P/P2P grading inside ``lb-interactive`` images.

```bash
uv run python interactive-bench/data-pipeline/reference/reference_test.py --limit 1
```
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from _lib import paths  # noqa: E402
from _lib.docker_util import docker, image_exists  # noqa: E402
from _lib.io_util import append_drop, load_json, load_jsonl, write_jsonl  # noqa: E402
from _lib.spec import build_interactive_spec  # noqa: E402

_DOCKER_REF_SCRIPT = r"""
set -euo pipefail
cd /work/repo
python3 <<'PY'
import json, os, re, subprocess, sys
from pathlib import Path

task = json.loads(Path("/opt/lb/task.json").read_text())
test_patch = task["test_patch"]
code_patch = task.get("gold_code_patch") or task.get("patch") or ""
test_files = [f for f in (task.get("test_files") or []) if f.endswith(".py")]
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
    env = os.environ.copy()
    # Common monorepo layout: backend/app, src/pkg, etc.
    path_bits = []
    for f in files:
        parts = Path(f).parts
        if parts:
            path_bits.append(parts[0])
    for root in sorted(set(path_bits)):
        root_path = Path(root)
        if root_path.is_dir() and (
            (root_path / "pyproject.toml").exists()
            or (root_path / "setup.py").exists()
            or (root_path / "app").is_dir()
            or (root_path / "src").is_dir()
        ):
            path_bits_add = str(root_path.resolve())
            env["PYTHONPATH"] = path_bits_add + (
                ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
    # Also repo root
    env["PYTHONPATH"] = str(Path("/work/repo").resolve()) + (
        ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    args = [str(py), "-m", "pytest", "-v", "--no-header",
            "-p", "no:cacheprovider", "-p", "pytester",
            "-o", "addopts=", "-o", "minversion=0", *files]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
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
f2p = sorted(
    t for t, r in post.items()
    if r == "PASSED" and pre.get(t) != "PASSED"
)
p2p = sorted(t for t, r in post.items() if r == "PASSED" and pre.get(t) == "PASSED")
print(json.dumps({
    "FAIL_TO_PASS": f2p,
    "PASS_TO_PASS": p2p,
    "n_tests_seen": len(post),
    "n_pre": len(pre),
}))
PY
"""


def reference_docker(instance: dict, timeout: int = 600) -> dict:
    task_id = instance["task_id"]
    tag = instance.get("docker_image") or paths.task_image(task_id)
    if not image_exists(tag):
        return {"error": f"missing image {tag}"}

    task = build_interactive_spec(instance)
    # Prefer concrete test file paths from enrich
    if instance.get("test_files"):
        task["test_files"] = instance["test_files"]

    with tempfile.TemporaryDirectory(prefix="lb-ix-ref-") as tmp:
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
        return {"error": (result.stderr or result.stdout or "docker ref failed")[-800:]}

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
        return {"error": f"no JSON from container: {(result.stdout or '')[-500:]}"}
    return {
        "FAIL_TO_PASS": payload.get("FAIL_TO_PASS", []),
        "PASS_TO_PASS": payload.get("PASS_TO_PASS", []),
        "n_tests_seen": payload.get("n_tests_seen", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--in", dest="inp", type=Path, default=paths.DEFAULT_ENV_JSONL)
    parser.add_argument("--out", type=Path, default=paths.DEFAULT_REFERENCE_JSONL)
    parser.add_argument("--drop", type=Path, default=paths.DEFAULT_REFERENCE_DROP)
    args = parser.parse_args()

    done = {r["task_id"] for r in load_jsonl(args.out) if r.get("task_id")}
    if args.drop.exists():
        for entry in load_json(args.drop).get("dropped") or []:
            if isinstance(entry, dict) and entry.get("task_id"):
                done.add(entry["task_id"])

    kept: list[dict] = []
    for inst in load_jsonl(args.inp):
        if args.limit and len(kept) >= args.limit:
            break
        tid = inst.get("task_id")
        if not tid or tid in done:
            continue
        try:
            result = reference_docker(inst, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            append_drop(args.drop, tid, f"error: {exc}")
            done.add(tid)
            print(f"  drop {tid}: error: {exc}")
            continue
        if result.get("error"):
            append_drop(args.drop, tid, result["error"])
            done.add(tid)
            print(f"  drop {tid}: {result['error'][:120]}")
            continue
        f2p = result.get("FAIL_TO_PASS") or []
        if not f2p:
            append_drop(args.drop, tid, "no FAIL_TO_PASS tests")
            done.add(tid)
            print(f"  drop {tid}: no F2P")
            continue
        out = dict(inst)
        out["fail_to_pass"] = f2p
        out["pass_to_pass"] = result.get("PASS_TO_PASS") or []
        out["FAIL_TO_PASS"] = f2p
        out["PASS_TO_PASS"] = out["pass_to_pass"]
        out["n_tests_seen"] = result.get("n_tests_seen", 0)
        kept.append(out)
        done.add(tid)
        print(f"  ok {tid}: f2p={len(f2p)} p2p={len(out['pass_to_pass'])}")

    existing = {r["task_id"]: r for r in load_jsonl(args.out) if r.get("task_id")}
    for r in kept:
        existing[r["task_id"]] = r
    write_jsonl(args.out, existing.values(), append=False)
    print(f"reference {len(kept)} new; total {len(existing)}; out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
