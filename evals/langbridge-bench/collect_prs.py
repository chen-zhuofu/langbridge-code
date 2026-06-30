"""Stage 1 of the dataset pipeline: collect SWE-bench-style task instances from GitHub.

This follows the high-level recipe (see README.md):

  1. Look at *merged* PRs on a repo's default branch.
  2. Keep only PRs that
       - modify fewer than --max-files files,
       - touch BOTH code and test files (so the test diff can act as a hidden grader),
       - are linked to an issue via a closing keyword (fixes/closes/resolves #N).
  3. For each kept PR emit one instance in the SWE-bench schema:
       instance_id, repo, base_commit, patch, test_patch, problem_statement,
       hints_text, created_at, FAIL_TO_PASS (empty here), PASS_TO_PASS (empty here).

FAIL_TO_PASS / PASS_TO_PASS are filled in later by reference_test.py, which actually
runs the tests pre-fix and post-fix.

Auth: set GITHUB_TOKEN (or GH_TOKEN) to lift the 60 req/hour anonymous limit to
5000/hour. The collector reads the rate-limit headers and stops cleanly (saving
what it has) when it runs out, so it is safe to re-run.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


API = "https://api.github.com"

# Files we treat as "tests" (everything else that is code is a "code" change).
TEST_PATH_RE = re.compile(r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*\.py$|_test\.[a-z]+$|\.spec\.[a-z]+$|conftest\.py$", re.IGNORECASE)

# "fixes #12", "closes #3", "resolves owner/repo#9" — the issue-linkage signal.
CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+(?:[\w.\-/]+)?#(\d+)", re.IGNORECASE)


def gh_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "langbridge-dataset-collector",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("LANGBRIDGE_GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class RateLimited(Exception):
    """Raised when the GitHub API rate limit is exhausted."""


def api_get(url, accept=None):
    """GET a GitHub API URL. Returns (parsed_or_text, headers). Raises RateLimited on 403/429 with no quota."""
    headers = gh_headers()
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            info = response.headers
            if accept and "diff" in accept:
                return body.decode("utf-8", "replace"), info
            return json.loads(body.decode("utf-8")), info
    except urllib.error.HTTPError as error:
        remaining = error.headers.get("X-RateLimit-Remaining") if error.headers else None
        if error.code in (403, 429) and remaining == "0":
            reset = error.headers.get("X-RateLimit-Reset", "?")
            raise RateLimited(f"rate limit hit; resets at epoch {reset}") from error
        if error.code in (404, 410):
            return None, error.headers
        raise


def warn_budget(info):
    """Print remaining quota so a run's cost is visible."""
    if info is None:
        return
    remaining = info.get("X-RateLimit-Remaining")
    if remaining is not None:
        print(f"    [rate] {remaining} GitHub API calls left this window", file=sys.stderr)


def default_branch(repo):
    data, info = api_get(f"{API}/repos/{repo}")
    warn_budget(info)
    if not data:
        raise ValueError(f"repo not found: {repo}")
    return data["default_branch"]


def iter_merged_prs(repo, base, max_scan):
    """Yield merged PR objects (default branch), newest first, up to max_scan scanned."""
    scanned = 0
    page = 1
    while scanned < max_scan:
        url = (
            f"{API}/repos/{repo}/pulls"
            f"?state=closed&base={base}&sort=updated&direction=desc&per_page=100&page={page}"
        )
        prs, info = api_get(url)
        warn_budget(info)
        if not prs:
            return
        for pr in prs:
            scanned += 1
            if pr.get("merged_at") and pr.get("merge_commit_sha"):
                yield pr
            if scanned >= max_scan:
                return
        page += 1


def linked_issue_numbers(pr):
    text = f"{pr.get('title', '')}\n{pr.get('body') or ''}"
    return [int(n) for n in dict.fromkeys(CLOSES_RE.findall(text))]


def classify_files(files):
    """Split a commit's files list into (test_files, code_files) by path."""
    test_files, code_files = [], []
    for entry in files:
        name = entry.get("filename", "")
        (test_files if TEST_PATH_RE.search(name) else code_files).append(entry)
    return test_files, code_files


def file_to_diff(entry):
    """Rebuild a git-applyable unified diff for one file from the commit `files` entry."""
    status = entry.get("status")
    path = entry["filename"]
    patch = entry.get("patch")
    if patch is None:
        # Binary or rename-without-content: skip the hunk body but keep a header so
        # the file count stays honest. git apply tolerates an empty rename header.
        return None
    old = entry.get("previous_filename", path)
    if status == "added":
        header = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
    elif status == "removed":
        header = f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n"
    elif status == "renamed":
        header = f"diff --git a/{old} b/{path}\nrename from {old}\nrename to {path}\n--- a/{old}\n+++ b/{path}\n"
    else:
        header = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    body = patch if patch.endswith("\n") else patch + "\n"
    return header + body


def build_patches(files):
    """Return (code_patch, test_patch) as unified-diff strings."""
    test_files, code_files = classify_files(files)
    code_patch = "".join(d for d in (file_to_diff(f) for f in code_files) if d)
    test_patch = "".join(d for d in (file_to_diff(f) for f in test_files) if d)
    return code_patch, test_patch, test_files, code_files


def fetch_issue_text(repo, number):
    data, info = api_get(f"{API}/repos/{repo}/issues/{number}")
    warn_budget(info)
    if not data or "pull_request" in data:  # skip if it's actually a PR, not an issue
        return None
    title = data.get("title", "")
    body = data.get("body") or ""
    return f"{title}\n\n{body}".strip()


def make_instance(repo, pr, max_files):
    """Turn one merged PR into a SWE-bench instance dict, or None if it fails a filter."""
    issues = linked_issue_numbers(pr)
    if not issues:
        return None, "no linked issue"

    merge_sha = pr["merge_commit_sha"]
    commit, info = api_get(f"{API}/repos/{repo}/commits/{merge_sha}")
    warn_budget(info)
    if not commit:
        return None, "merge commit missing"
    parents = commit.get("parents", [])
    if not parents:
        return None, "no parent commit"
    base_commit = parents[0]["sha"]
    files = commit.get("files", [])

    if len(files) >= max_files:
        return None, f"too many files ({len(files)})"

    code_patch, test_patch, test_files, code_files = build_patches(files)
    if not test_files:
        return None, "no test changes"
    if not code_files:
        return None, "no code changes"
    if not code_patch.strip() or not test_patch.strip():
        return None, "empty reconstructed patch"

    problem_parts = []
    for number in issues:
        text = fetch_issue_text(repo, number)
        if text:
            problem_parts.append(text)
    if not problem_parts:
        return None, "linked issue had no usable text"
    problem_statement = "\n\n---\n\n".join(problem_parts)

    owner, name = repo.split("/")
    instance_id = f"{owner}__{name}-{pr['number']}"
    instance = {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": base_commit,
        "patch": code_patch,
        "test_patch": test_patch,
        "problem_statement": problem_statement,
        "hints_text": "",
        "created_at": pr.get("created_at", ""),
        "version": "",
        "FAIL_TO_PASS": [],
        "PASS_TO_PASS": [],
        "environment_setup_commit": base_commit,
        # provenance, not part of the SWE-bench schema but handy for auditing
        "_pr_url": pr.get("html_url", ""),
        "_linked_issues": issues,
        "_num_files": len(files),
    }
    return instance, "ok"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", dest="repos", default=[], help="owner/name (repeatable)")
    parser.add_argument("--repos-file", help="file with one owner/name per line")
    parser.add_argument("--max-files", type=int, default=15, help="keep PRs touching fewer than this many files")
    parser.add_argument("--max-per-repo", type=int, default=5, help="stop after this many accepted instances per repo")
    parser.add_argument("--max-scan", type=int, default=100, help="how many closed PRs to scan per repo")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "out" / "instances.jsonl"))
    parser.add_argument("--append", action="store_true", help="append to --out instead of truncating")
    args = parser.parse_args()

    repos = list(args.repos)
    if args.repos_file:
        repos += [line.strip() for line in Path(args.repos_file).read_text().splitlines() if line.strip() and not line.startswith("#")]
    if not repos:
        parser.error("give at least one --repo or --repos-file")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.append:
        out_path.write_text("", encoding="utf-8")  # truncate; write each instance as we go

    instances = []
    rejected = {}
    try:
        for repo in repos:
            print(f"\n=== {repo} ===")
            try:
                base = default_branch(repo)
            except Exception as error:  # noqa: BLE001 - skip bad repo names
                print(f"  skip repo: {error}")
                continue
            kept = 0
            for pr in iter_merged_prs(repo, base, args.max_scan):
                if kept >= args.max_per_repo:
                    break
                try:
                    instance, reason = make_instance(repo, pr, args.max_files)
                except Exception as error:  # noqa: BLE001 - one bad PR should not kill the run
                    rejected[f"error: {error}"] = rejected.get(f"error: {error}", 0) + 1
                    continue
                if instance is None:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                instances.append(instance)
                with out_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(instance) + "\n")
                kept += 1
                print(f"  + {instance['instance_id']}  files={instance['_num_files']} issues={instance['_linked_issues']}")
    except RateLimited as limited:
        print(f"\n[stopped] {limited}\nSet GITHUB_TOKEN to raise the limit. Saving what we collected.", file=sys.stderr)

    print(f"\nWrote {len(instances)} instances to {out_path}")
    if rejected:
        print("Rejected (by reason):")
        for reason, count in sorted(rejected.items(), key=lambda item: -item[1]):
            print(f"  {count:4d}  {reason}")
    print("\nNext: run reference_test.py to fill FAIL_TO_PASS / PASS_TO_PASS for Python repos.")


if __name__ == "__main__":
    main()
