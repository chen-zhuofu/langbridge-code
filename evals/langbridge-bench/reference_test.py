"""Stage 3 of the dataset pipeline: the two-stage reference test.

For each collected instance this:

  1. checks out the repo at base_commit,
  2. verifies the reconstructed `patch` and `test_patch` actually apply (git apply --check),
  3. (with --run) builds a throwaway venv, installs the project, then runs the
     tests twice:
       - pre-fix:  base code + test_patch applied   -> tests that FAIL here
       - post-fix: also apply the code patch         -> tests that now PASS
     FAIL_TO_PASS = failed pre-fix AND passed post-fix   (the issue-resolution signal)
     PASS_TO_PASS = passed in both                       (the regression signal)
  4. drops instances with no FAIL_TO_PASS (no resolution signal == not gradable).

Only Python/pytest projects are supported by --run; --apply-only works for any repo
and is the cheap correctness check on the reconstructed diffs.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


# Shallow checkouts have no git tags, so setuptools-scm versions the project as
# 0.1.devN. Some projects (notably pytest) then reject themselves via `minversion`.
# Pretend a high version at build time and override minversion at run time.
BUILD_ENV = {**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": "9999.0.0"}
TEST_EXTRA_NAMES = {"dev", "test", "tests", "testing"}


TEST_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
# pytest -v line:  path/to/test.py::TestClass::test_name PASSED
PYTEST_LINE_RE = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR)\b")


def run(args, cwd=None, timeout=None, check=False, env=None):
    return subprocess.run(args, cwd=cwd, timeout=timeout, check=check, capture_output=True, text=True, env=env)


def shallow_checkout(repo, base_commit, repo_dir):
    repo_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"
    run(["git", "init", "-q"], cwd=repo_dir, check=True)
    run(["git", "remote", "add", "origin", url], cwd=repo_dir, check=True)
    run(["git", "fetch", "-q", "--depth", "1", "origin", base_commit], cwd=repo_dir, check=True)
    run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=repo_dir, check=True)


def apply_patch(repo_dir, patch_text, check_only=False):
    flags = ["--check"] if check_only else []
    # tolerate trailing-whitespace noise from the reconstructed diffs.
    result = subprocess.run(
        ["git", "apply", *flags, "--whitespace=nowarn"],
        cwd=repo_dir, input=patch_text, capture_output=True, text=True,
    )
    return result.returncode == 0, result.stderr.strip()


def test_files_in_patch(test_patch):
    return TEST_FILE_RE.findall(test_patch)


def test_extras(repo_dir):
    """Find optional-dependency groups that look like test deps (best effort)."""
    pyproject = repo_dir / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - malformed/templated pyproject
        return []
    groups = data.get("project", {}).get("optional-dependencies", {})
    return [name for name in groups if name.lower() in TEST_EXTRA_NAMES]


def make_venv(repo_dir):
    venv = repo_dir / ".refvenv"
    run(["uv", "venv", str(venv)], cwd=repo_dir, check=True)
    py = venv / "bin" / "python"
    extras = test_extras(repo_dir)
    target = f".[{','.join(extras)}]" if extras else "."
    # install the project (editable) with its test extras, plus pytest; best effort.
    install = run(["uv", "pip", "install", "--python", str(py), "-e", target, "pytest"], cwd=repo_dir, timeout=900, env=BUILD_ENV)
    if install.returncode != 0:
        # fall back to non-editable without extras so we can still try to run.
        run(["uv", "pip", "install", "--python", str(py), ".", "pytest"], cwd=repo_dir, timeout=900, env=BUILD_ENV)
    return py


def run_pytest(py, repo_dir, test_files, timeout):
    if not test_files:
        return {}
    # -p pytester: some suites (e.g. pytest's own) register ini options via the
    # pytester plugin; force-load it so strict-config does not reject them.
    args = [str(py), "-m", "pytest", "-v", "--no-header", "-p", "no:cacheprovider",
            "-p", "pytester", "-o", "addopts=", "-o", "minversion=0", *test_files]
    result = run(args, cwd=repo_dir, timeout=timeout, env=BUILD_ENV)
    outcomes = {}
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        match = PYTEST_LINE_RE.match(line.strip())
        if match:
            outcomes[match.group(1)] = match.group(2)
    return outcomes


def reference_test_one(instance, work_dir, do_run, timeout):
    repo = instance["repo"]
    repo_dir = work_dir / instance["instance_id"]
    if repo_dir.exists():
        run(["rm", "-rf", str(repo_dir)])
    shallow_checkout(repo, instance["base_commit"], repo_dir)

    ok_test, err_test = apply_patch(repo_dir, instance["test_patch"], check_only=True)
    ok_code, err_code = apply_patch(repo_dir, instance["patch"], check_only=True)
    status = {"applies_test_patch": ok_test, "applies_patch": ok_code}
    if not (ok_test and ok_code):
        status["error"] = f"test_patch: {err_test or 'ok'} | patch: {err_code or 'ok'}"
        return status

    if not do_run:
        return status

    test_files = test_files_in_patch(instance["test_patch"])
    py = make_venv(repo_dir)

    # pre-fix: apply only the test patch.
    applied, err = apply_patch(repo_dir, instance["test_patch"])
    if not applied:
        status["error"] = f"could not apply test_patch for run: {err}"
        return status
    pre = run_pytest(py, repo_dir, test_files, timeout)

    # post-fix: also apply the code patch.
    applied, err = apply_patch(repo_dir, instance["patch"])
    if not applied:
        status["error"] = f"could not apply code patch for run: {err}"
        return status
    post = run_pytest(py, repo_dir, test_files, timeout)

    fail_to_pass = sorted(t for t, r in post.items() if r == "PASSED" and pre.get(t) in ("FAILED", "ERROR"))
    pass_to_pass = sorted(t for t, r in post.items() if r == "PASSED" and pre.get(t) == "PASSED")
    status.update({
        "FAIL_TO_PASS": fail_to_pass,
        "PASS_TO_PASS": pass_to_pass,
        "n_tests_seen": len(post),
    })
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", default=str(Path(__file__).resolve().parent / "out" / "instances.jsonl"))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "out" / "instances_validated.jsonl"))
    parser.add_argument("--run", action="store_true", help="actually build envs and run tests (Python/pytest only)")
    parser.add_argument("--timeout", type=int, default=600, help="per pytest invocation timeout (s)")
    parser.add_argument("--limit", type=int, default=0, help="only process the first N instances")
    args = parser.parse_args()

    instances = [json.loads(line) for line in Path(args.instances).read_text().splitlines() if line.strip()]
    if args.limit:
        instances = instances[: args.limit]

    work_dir = Path(tempfile.mkdtemp(prefix="reftest-"))
    kept = []
    for index, instance in enumerate(instances, start=1):
        print(f"\n[{index}/{len(instances)}] {instance['instance_id']}")
        try:
            status = reference_test_one(instance, work_dir, args.run, args.timeout)
        except subprocess.TimeoutExpired:
            print("  timed out"); continue
        except subprocess.CalledProcessError as failure:
            print(f"  setup failed: {failure.stderr or failure}"); continue

        print(f"  applies: test_patch={status.get('applies_test_patch')} patch={status.get('applies_patch')}")
        if not (status.get("applies_test_patch") and status.get("applies_patch")):
            print(f"  DROP (patch does not apply): {status.get('error')}")
            continue
        if args.run:
            f2p = status.get("FAIL_TO_PASS", [])
            p2p = status.get("PASS_TO_PASS", [])
            print(f"  FAIL_TO_PASS={len(f2p)} PASS_TO_PASS={len(p2p)} (tests seen={status.get('n_tests_seen')})")
            if not f2p:
                print("  DROP (no FAIL_TO_PASS signal)")
                continue
            instance["FAIL_TO_PASS"] = f2p
            instance["PASS_TO_PASS"] = p2p
        kept.append(instance)

    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8") as handle:
        for instance in kept:
            handle.write(json.dumps(instance) + "\n")
    print(f"\nKept {len(kept)}/{len(instances)} instances -> {out_path}")


if __name__ == "__main__":
    main()
