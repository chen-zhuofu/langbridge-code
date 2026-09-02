"""grade_checkout.py — grade a candidate diff against the checkout at cwd.

Used by the Docker eval runner inside a **fresh** grade container (same task
image as the agent, after the agent container is torn down). Expects cwd to
already be at base_commit (clean).

  python -m grade_checkout \\
    --spec /tmp/spec.json --diff /tmp/candidate.diff --out /tmp/grade.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def grade_checkout(
    *,
    repo_dir: Path,
    spec: dict,
    candidate_diff: str,
    timeout: int,
) -> dict:
    """Apply test_patch + candidate, run pytest + static analysis, return grade dict."""
    from util import bench, dimensions, ref_helpers

    ref = ref_helpers
    py = repo_dir / ".refvenv" / "bin" / "python"
    if not py.exists():
        return {"resolved": False, "status": "no_venv", "error": str(py)}

    ok, err = ref.apply_patch(repo_dir, spec["test_patch"])
    if not ok:
        return {"resolved": False, "status": f"test_patch_apply_failed: {err}"}

    cand = bench.split_diff(candidate_diff)
    changed_files = dimensions.changed_py_files(cand)
    # Baseline BEFORE the candidate lands: only newly introduced findings count.
    static_baseline = dimensions.static_baseline(str(repo_dir), spec, changed_files)
    if cand.strip():
        ok, err = ref.apply_patch(repo_dir, cand)
        if not ok:
            return {"resolved": False, "status": f"candidate_patch_apply_failed: {err}"}

    static = dimensions.score_static_analysis(
        str(repo_dir), spec, changed_files, static_baseline
    )

    started = time.perf_counter()
    outcomes = ref.run_pytest(py, repo_dir, spec["test_files"], timeout)
    test_latency_s = time.perf_counter() - started

    f2p = list(spec.get("fail_to_pass") or [])
    p2p = list(spec.get("pass_to_pass") or [])
    f2p_passed = sum(1 for t in f2p if outcomes.get(t) == "PASSED")
    regressions = [t for t in p2p if t in outcomes and outcomes.get(t) != "PASSED"]
    resolved = bool(f2p) and f2p_passed == len(f2p) and not regressions

    return {
        "resolved": resolved,
        "f2p_passed": f2p_passed,
        "f2p_total": len(f2p),
        "regressions": regressions,
        "status": "graded",
        "test_latency_s": round(test_latency_s, 3),
        "static_analysis": static,
        "outcomes": outcomes,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="path to task spec JSON")
    parser.add_argument("--diff", required=True, help="path to candidate.diff")
    parser.add_argument("--out", required=True, help="path to write grade JSON")
    parser.add_argument("--repo", default=".", help="checkout directory (default cwd)")
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args(argv)

    from langbridge_code.settings import GRADE_TIMEOUT_SECONDS

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    candidate = Path(args.diff).read_text(encoding="utf-8")
    timeout = args.timeout if args.timeout is not None else GRADE_TIMEOUT_SECONDS
    result = grade_checkout(
        repo_dir=Path(args.repo).resolve(),
        spec=spec,
        candidate_diff=candidate,
        timeout=timeout,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"resolved": result.get("resolved"), "status": result.get("status")}))
    return 0 if result.get("status") == "graded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
