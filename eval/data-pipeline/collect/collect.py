"""Stage 1 — collect SWE-bench-style task instances from GitHub PRs.

Reads ``eval/data-pipeline/collect/in/repos.md`` by default.
Writes ``eval/data-pipeline/collect/out/instances.jsonl``.

Resume: skips task_ids already present in ``collect/out/instances.jsonl``.
Does not read any drop.json.

Metadata (alongside patches):
  ``_pr_url``, ``_linked_issues``, ``_github_issue_urls``,
  ``_jira_url`` / ``_jira_key`` (extracted from PR/issue bodies).

``task_type`` / ``difficulty`` are labeled later in curate (with F2P).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

_PIPELINE = Path(__file__).resolve().parents[1]
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from _lib import paths  # noqa: E402
from _lib.io import existing_task_ids_from_jsonl, write_json  # noqa: E402
from _lib.repo_balance import allocate  # noqa: E402

API = "https://api.github.com"

TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*\.py$|_test\.[a-z]+$|\.spec\.[a-z]+$|conftest\.py$",
    re.IGNORECASE,
)
CLOSES_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+(?:[\w.\-/]+)?#(\d+)",
    re.IGNORECASE,
)
# Jira: https://jira.example.com/browse/PROJ-123 or bare PROJ-123 in link context
JIRA_URL_RE = re.compile(
    r"https?://[^\s)>\]]+/browse/([A-Z][A-Z0-9]+-\d+)",
    re.IGNORECASE,
)
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def gh_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "langbridge-dataset-collector",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("LANGBRIDGE_GH_TOKEN")
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class RateLimited(Exception):
    """Raised when the GitHub API rate limit is exhausted."""


def api_get(url, accept=None):
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
    if info is None:
        return
    remaining = info.get("X-RateLimit-Remaining")
    if remaining is not None:
        print(f"    [rate] {remaining} GitHub API calls left this window", file=sys.stderr)


def parse_repos_md(path: Path) -> list[str]:
    """Parse repos.md: one ``owner/name`` per line; ``#`` comments / bullets ok."""
    repos = []
    text = re.sub(
        r"<!--.*?-->",
        "",
        path.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("-*").strip()
        line = line.strip("`")
        if "/" in line and " " not in line.split("/")[0]:
            # take first token that looks like owner/repo
            token = line.split()[0].strip("()[],")
            if token.count("/") == 1:
                repos.append(token)
    return repos


def repo_targets(
    total: int,
    repos: list[str],
    progress: dict[str, dict[str, int]] | None = None,
) -> dict[str, int]:
    """Allocate ``total`` final tasks across configured repos.

    ``progress`` accounts for successful and in-flight tasks in the current
    pipeline run. Tasks from repos no longer configured still consume target
    slots, while configured repos are topped up as evenly as possible.
    """
    progress = progress or {}
    current = {
        repo: int(progress.get(repo, {}).get("done", 0))
        + int(progress.get(repo, {}).get("pending", 0))
        for repo in repos
    }
    active = [
        repo
        for repo in repos
        if not bool(progress.get(repo, {}).get("exhausted", False))
    ]
    if not active:
        return current
    outside = sum(
        int(info.get("done", 0)) + int(info.get("pending", 0))
        for repo, info in progress.items()
        if repo not in current
    )
    fixed = sum(current[repo] for repo in repos if repo not in active)
    active_targets = allocate(
        max(0, total - outside - fixed),
        active,
        minimums={repo: current[repo] for repo in active},
    )
    return {
        repo: active_targets.get(repo, current[repo])
        for repo in repos
    }


def default_branch(repo):
    data, info = api_get(f"{API}/repos/{repo}")
    warn_budget(info)
    if not data:
        raise ValueError(f"repo not found: {repo}")
    return data["default_branch"]


def iter_merged_prs(repo, base, max_scan):
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


def extract_jira(texts: list[str]) -> tuple[str | None, str | None]:
    """Return (jira_url, jira_key) from the first match across texts."""
    blob = "\n".join(texts)
    url_match = JIRA_URL_RE.search(blob)
    if url_match:
        return url_match.group(0), url_match.group(1).upper()
    # Prefer keys that appear near "jira" to reduce false positives.
    for match in JIRA_KEY_RE.finditer(blob):
        start = max(0, match.start() - 40)
        window = blob[start : match.end() + 10].lower()
        if "jira" in window or "atlassian" in window:
            return None, match.group(1).upper()
    return None, None


def classify_files(files):
    test_files, code_files = [], []
    for entry in files:
        name = entry.get("filename", "")
        (test_files if TEST_PATH_RE.search(name) else code_files).append(entry)
    return test_files, code_files


def file_to_diff(entry):
    status = entry.get("status")
    path = entry["filename"]
    patch = entry.get("patch")
    if patch is None:
        return None
    old = entry.get("previous_filename", path)
    if status == "added":
        header = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
    elif status == "removed":
        header = f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n"
    elif status == "renamed":
        header = (
            f"diff --git a/{old} b/{path}\nrename from {old}\nrename to {path}\n"
            f"--- a/{old}\n+++ b/{path}\n"
        )
    else:
        header = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    body = patch if patch.endswith("\n") else patch + "\n"
    return header + body


def build_patches(files):
    test_files, code_files = classify_files(files)
    code_patch = "".join(d for d in (file_to_diff(f) for f in code_files) if d)
    test_patch = "".join(d for d in (file_to_diff(f) for f in test_files) if d)
    return code_patch, test_patch, test_files, code_files


def fetch_issue(repo, number):
    data, info = api_get(f"{API}/repos/{repo}/issues/{number}")
    warn_budget(info)
    if not data or "pull_request" in data:
        return None
    return data


def make_instance(repo, pr, max_files):
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
    issue_bodies = [f"{pr.get('title', '')}\n{pr.get('body') or ''}"]
    github_issue_urls = []
    for number in issues:
        data = fetch_issue(repo, number)
        if not data:
            continue
        title = data.get("title", "")
        body = data.get("body") or ""
        problem_parts.append(f"{title}\n\n{body}".strip())
        issue_bodies.append(f"{title}\n{body}")
        if data.get("html_url"):
            github_issue_urls.append(data["html_url"])
    if not problem_parts:
        return None, "linked issue had no usable text"
    problem_statement = "\n\n---\n\n".join(problem_parts)

    jira_url, jira_key = extract_jira(issue_bodies)

    owner, name = repo.split("/")
    instance_id = f"{owner}__{name}-{pr['number']}"
    instance = {
        "instance_id": instance_id,
        "task_id": instance_id,
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
        "_pr_url": pr.get("html_url", ""),
        "_linked_issues": issues,
        "_github_issue_urls": github_issue_urls,
        "_num_files": len(files),
    }
    if jira_url:
        instance["_jira_url"] = jira_url
    if jira_key:
        instance["_jira_key"] = jira_key
    return instance, "ok"


_MAX_FILES = 15
_MAX_PER_REPO = 5
_MAX_SCAN = 2000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="collect this many new instances, spread evenly across repos "
        "(0 = up to the per-repo default)",
    )
    parser.add_argument(
        "--balance-target",
        type=int,
        default=0,
        help="final bench target to balance across repos (used by run_pipeline)",
    )
    parser.add_argument(
        "--repo-progress-json",
        default="{}",
        help="JSON {repo: {done, pending}} used with --balance-target",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        help="optional machine-readable per-repo collection summary",
    )
    args = parser.parse_args()
    if args.limit < 0 or args.balance_target < 0:
        parser.error("--limit and --balance-target must be non-negative")
    try:
        progress = json.loads(args.repo_progress_json)
    except json.JSONDecodeError as error:
        parser.error(f"invalid --repo-progress-json: {error}")
    if not isinstance(progress, dict):
        parser.error("--repo-progress-json must decode to an object")

    repos_path = paths.DEFAULT_REPOS_MD
    if not repos_path.exists():
        parser.error(f"missing repos file: {repos_path}")
    repos = parse_repos_md(repos_path) if repos_path.suffix == ".md" else [
        line.strip()
        for line in repos_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    seen = set()
    repos = [r for r in repos if not (r in seen or seen.add(r))]
    if not repos:
        parser.error(f"no repos in {repos_path}")

    target = args.balance_target or args.limit
    targets = repo_targets(target, repos, progress) if target else {}
    current = {
        repo: int(progress.get(repo, {}).get("done", 0))
        + int(progress.get(repo, {}).get("pending", 0))
        for repo in repos
    }
    deficits = {
        repo: max(0, targets.get(repo, 0) - current.get(repo, 0))
        for repo in repos
    }
    if target:
        print(f"Repo targets: {targets}")
        print(f"Repo deficits: {deficits}")

    out_path = paths.DEFAULT_COLLECT_JSONL
    out_path.parent.mkdir(parents=True, exist_ok=True)
    known = existing_task_ids_from_jsonl(out_path)

    instances = []
    rejected = {}
    repo_summary = {}
    skipped_resume = 0
    try:
        for repo in repos:
            repo_limit = deficits.get(repo, _MAX_PER_REPO)
            if repo_limit <= 0:
                continue
            print(f"\n=== {repo} ===")
            try:
                base = default_branch(repo)
            except Exception as error:  # noqa: BLE001
                print(f"  skip repo: {error}")
                repo_summary[repo] = {
                    "requested": repo_limit,
                    "collected": 0,
                    "exhausted": True,
                }
                continue
            kept = 0
            for pr in iter_merged_prs(repo, base, _MAX_SCAN):
                if kept >= repo_limit:
                    break
                owner, name = repo.split("/")
                tid = f"{owner}__{name}-{pr['number']}"
                if tid in known:
                    skipped_resume += 1
                    continue
                try:
                    instance, reason = make_instance(repo, pr, _MAX_FILES)
                except Exception as error:  # noqa: BLE001
                    rejected[f"error: {error}"] = rejected.get(f"error: {error}", 0) + 1
                    continue
                if instance is None:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue

                instances.append(instance)
                known.add(instance["instance_id"])
                with out_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(instance) + "\n")
                kept += 1
                jira = instance.get("_jira_key") or "-"
                print(
                    f"  + {instance['instance_id']}  "
                    f"files={instance['_num_files']} "
                    f"issues={instance['_linked_issues']} jira={jira}"
                )
            repo_summary[repo] = {
                "requested": repo_limit,
                "collected": kept,
                "exhausted": kept < repo_limit,
            }
    except RateLimited as limited:
        print(
            f"\n[stopped] {limited}\nSet GITHUB_TOKEN to raise the limit. Saving what we collected.",
            file=sys.stderr,
        )

    print(f"\nWrote {len(instances)} new instances to {out_path}")
    if skipped_resume:
        print(f"Skipped {skipped_resume} already-collected task_ids (resume)")
    if rejected:
        print("Rejected (by reason):")
        for reason, count in sorted(rejected.items(), key=lambda item: -item[1]):
            print(f"  {count:4d}  {reason}")
    if args.summary_json:
        write_json(args.summary_json, {"repos": repo_summary})
    print("\nNext: eval/data-pipeline/env/build_env.py")


if __name__ == "__main__":
    main()
