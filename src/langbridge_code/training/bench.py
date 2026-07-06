"""bench.py — the test-based ground-truth judge (the "real judge").

Distilled from the neighbouring worktrial's SWE-bench-style bench. Correctness is
NOT decided by a hand-written oracle or by the agents themselves; it is decided by
the regression tests that shipped in a task's real fix commit. The candidate can
never supply its own tests, so it cannot game the judge.

A spec (one JSON per task, cached under the specs dir) holds:
  {
    "task_id": str,
    "base_commit": str,            # repo state where the bug still exists
    "problem_statement": str,      # what the agent is told to do
    "test_files": [path, ...],     # files to run
    "gold_code_patch": str,        # the real source fix (for reference / negatives)
    "test_patch": str,             # the HIDDEN golden regression tests
    "fail_to_pass": [nodeid, ...], # tests that pass only once the bug is fixed
    "pass_to_pass": [nodeid, ...], # pre-existing tests that must keep passing
    "status": "ok",
    "hard": bool                   # route to L5 in the PM/L5 evals
  }

grade_diff(spec, candidate_code_diff, repo) applies the hidden golden tests + the
candidate's source changes onto base_commit in a throwaway git worktree, runs the
tests, and returns resolution (all F2P pass) and regressions (any P2P broke).

This module is the pluggable seam between "which target repo / tasks" (a decision
left to config) and the eval/evolver code, which only ever calls a grader
callable `grade(task_id, candidate_diff) -> result`.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# The target repo (with git history) and the spec cache. Both env-overridable so
# the eval/evolver can be pointed at whatever task set the user picks.
TARGET_REPO = os.environ.get("LANGBRIDGE_TARGET_REPO", "")
SPECS_DIR = os.environ.get("LANGBRIDGE_SPECS_DIR", "")
# Files under this prefix are "tests" (hidden ground truth); everything else is
# candidate-editable source.
TEST_PREFIX = os.environ.get("LANGBRIDGE_TEST_PREFIX", "tests/")

PYTEST_BASE = [
    "python3", "-m", "pytest",
    "-o", "addopts=",
    "--continue-on-collection-errors",
    "--tb=no", "-q",
]


def _git(repo, *args, check=True):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def _make_worktree(repo, base_sha):
    tmp = tempfile.mkdtemp(prefix="lb_bench_")
    path = os.path.join(tmp, "wt")
    _git(repo, "worktree", "add", "--detach", path, base_sha)
    return path


def _remove_worktree(repo, path):
    _git(repo, "worktree", "remove", "--force", path, check=False)
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    _git(repo, "worktree", "prune", check=False)


def _apply(worktree, patch_text):
    if not patch_text.strip():
        return True
    for extra in ([], ["--3way"]):
        r = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", *extra],
            cwd=worktree, input=patch_text, capture_output=True, text=True,
        )
        if r.returncode == 0:
            return True
    return False


def _run_tests(worktree, test_files):
    """Run the given test files; return {nodeid: 'pass'|'fail'} via junit xml."""
    if not test_files:
        return {}
    xml_path = os.path.join(worktree, "_bench_junit.xml")
    subprocess.run(
        PYTEST_BASE + [f"--junit-xml={xml_path}"] + test_files,
        cwd=worktree, capture_output=True, text=True,
    )
    if not os.path.exists(xml_path):
        return {}
    results = {}
    tree = ET.parse(xml_path)
    for tc in tree.iter("testcase"):
        cls = tc.get("classname", "")
        name = tc.get("name", "")
        nodeid = f"{cls}.{name}" if cls else name
        failed = any(child.tag in ("failure", "error") for child in tc)
        results[nodeid] = "fail" if failed else "pass"
    os.remove(xml_path)
    return results


def _matches(nodeid, names):
    return any(nodeid == q or nodeid.endswith("." + q) for q in names)


def split_diff(patch_text, test_prefix=None):
    """Drop any test-file hunks from a candidate diff (it must not ship its own
    tests). Returns the source-only patch."""
    test_prefix = test_prefix or TEST_PREFIX
    out, keep = [], True
    for line in patch_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            m = re.search(r" b/(\S+)", line)
            keep = not (m and m.group(1).startswith(test_prefix))
        if keep:
            out.append(line)
    return "".join(out)


def grade_diff(spec, candidate_code_diff, repo=None):
    """Grade a candidate source diff against a spec's hidden tests.

    Returns {resolved, f2p_passed, f2p_total, regressions, status}.
    """
    repo = repo or TARGET_REPO
    if not spec or spec.get("status") != "ok":
        return {"resolved": False, "status": "no_spec"}
    if not repo:
        return {"resolved": False, "status": "no_repo"}

    candidate = split_diff(candidate_code_diff)
    wt = _make_worktree(repo, spec["base_commit"])
    try:
        if not _apply(wt, spec.get("test_patch", "")):
            return {"resolved": False, "status": "test_patch_apply_failed"}
        if candidate.strip() and not _apply(wt, candidate):
            return {"resolved": False, "status": "candidate_patch_apply_failed"}
        results = _run_tests(wt, spec["test_files"])
        f2p = spec.get("fail_to_pass", [])
        p2p = spec.get("pass_to_pass", [])
        f2p_passed = sum(1 for t in f2p if results.get(t) == "pass")
        regressions = [t for t in p2p if results.get(t) != "pass"]
        resolved = (f2p_passed == len(f2p)) and not regressions and bool(f2p)
        return {
            "resolved": resolved,
            "f2p_passed": f2p_passed,
            "f2p_total": len(f2p),
            "regressions": regressions,
            "status": "graded",
        }
    finally:
        _remove_worktree(repo, wt)


# --------------------------------------------------------------------------- #
# Spec loading.                                                                #
# --------------------------------------------------------------------------- #
def specs_dir() -> str:
    return SPECS_DIR or str(Path(__file__).resolve().parents[3] / "evals" / "langbridge-bench" / "specs")


def load_spec(task_id, directory=None):
    directory = directory or specs_dir()
    path = os.path.join(directory, f"{task_id}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def list_specs(directory=None, ok_only=True, hard=None):
    """Return the loaded specs in the dir. Filter by status/hard if asked."""
    directory = directory or specs_dir()
    if not os.path.isdir(directory):
        return []
    out = []
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(directory, fname)) as f:
            spec = json.load(f)
        if ok_only and spec.get("status") != "ok":
            continue
        if hard is not None and bool(spec.get("hard")) != hard:
            continue
        out.append(spec)
    return out


# --------------------------------------------------------------------------- #
# Spec building — derive F2P/P2P specs from a repo's real fix commits.         #
# Given an issue {task_id, fix_commit, title, body_summary, hard?}, the spec is #
# derived entirely from git history (SWE-bench style); no hand-written oracle.  #
# --------------------------------------------------------------------------- #
def _diff(repo, base, fix, pathspec=None):
    args = ["diff", base, fix]
    if pathspec:
        args += ["--", pathspec]
    return _git(repo, *args)


def _files_in_patch(patch):
    files = set()
    for m in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE):
        if m.group(1) != "/dev/null":
            files.add(m.group(1))
    return sorted(files)


def build_spec(issue, repo=None, test_prefix=None):
    """Derive and return a benchmark spec for one issue (or a status dict).

    base_commit = fix_commit^. code_patch / test_patch are the real fix split by
    test_prefix. F2P = tests passing after the fix that did NOT pass at base; P2P =
    tests passing both before and after. status == 'ok' iff there is >=1 F2P.
    """
    repo = repo or TARGET_REPO
    test_prefix = test_prefix or TEST_PREFIX
    tid = issue.get("task_id") or issue.get("number")
    fix = _git(repo, "rev-parse", "--verify", f"{issue['fix_commit']}^{{commit}}").strip()
    base = _git(repo, "rev-parse", f"{fix}^").strip()

    full = _diff(repo, base, fix)
    test_patch = "".join(
        _hunks_for(full, lambda f: f.startswith(test_prefix))
    )
    code_patch = "".join(
        _hunks_for(full, lambda f: not f.startswith(test_prefix))
    )
    if not test_patch.strip():
        return {"task_id": tid, "status": "no_test_patch", "base_commit": base}

    test_files = _files_in_patch(test_patch)
    wt = _make_worktree(repo, base)
    try:
        base_pass = {t for t, o in _run_tests(wt, test_files).items() if o == "pass"}
        if not _apply(wt, test_patch) or not _apply(wt, code_patch):
            return {"task_id": tid, "status": "patch_apply_failed"}
        after = _run_tests(wt, test_files)
        if not after:
            return {"task_id": tid, "status": "collection_error"}
        fail_to_pass = sorted(t for t, o in after.items() if o == "pass" and t not in base_pass)
        pass_to_pass = sorted(t for t, o in after.items()
                              if o == "pass" and t in base_pass)
        status = "ok" if fail_to_pass else "no_f2p"
        return {
            "task_id": tid,
            "status": status,
            "base_commit": base,
            "fix_commit": fix,
            "problem_statement": f"{issue.get('title','')}\n\n{issue.get('body_summary','')}".strip(),
            "test_files": test_files,
            "gold_code_patch": code_patch,
            "test_patch": test_patch,
            "fail_to_pass": fail_to_pass,
            "pass_to_pass": pass_to_pass,
            "hard": bool(issue.get("hard")),
        }
    finally:
        _remove_worktree(repo, wt)


def _hunks_for(full_diff, keep_file):
    """Yield the per-file sections of a unified diff whose path keep_file() accepts."""
    section, keep = [], False
    for line in full_diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if section and keep:
                yield "".join(section)
            m = re.search(r" b/(\S+)", line)
            keep = bool(m and keep_file(m.group(1)))
            section = [line]
        else:
            section.append(line)
    if section and keep:
        yield "".join(section)


def build_specs(issues, repo=None, directory=None):
    """Build + cache specs for a list of issues. Returns (ok_count, statuses)."""
    directory = directory or specs_dir()
    os.makedirs(directory, exist_ok=True)
    statuses = {}
    ok = 0
    for issue in issues:
        spec = build_spec(issue, repo)
        tid = spec.get("task_id")
        statuses[tid] = spec.get("status")
        if spec.get("status") == "ok":
            ok += 1
        with open(os.path.join(directory, f"{tid}.json"), "w") as f:
            json.dump(spec, f, indent=2)
    return ok, statuses


# --------------------------------------------------------------------------- #
# Grader factory — the pluggable seam used by the eval/evolver code.           #
# --------------------------------------------------------------------------- #
def make_git_grader(repo=None, directory=None):
    """Return grade(task_id, candidate_diff) -> result, bound to a repo + specs."""
    repo = repo or TARGET_REPO

    def grade(task_id, candidate_diff):
        spec = load_spec(task_id, directory)
        if spec is None:
            return {"resolved": False, "status": "no_spec"}
        return grade_diff(spec, candidate_diff, repo)

    return grade
