# Dataset pipeline: build SWE-bench-style tasks from GitHub

This turns real, merged GitHub pull requests into training/eval tasks in the
**SWE-bench schema** — the same schema the grader in `evals/swebench/` consumes.

Each task is one bug fix. The model is given the issue text and the repo at the
commit *before* the fix, and must produce a patch. A hidden test decides if the
fix is correct.

## The recipe (and where each step lives)

This mirrors the standard "construct high-quality RL environments" recipe:

| Step | What it does | Here |
| --- | --- | --- |
| 1. Collect PRs | Keep merged PRs that touch **both code and tests**, change **< 15 files**, and **link an issue** (`fixes #N`). | `collect_prs.py` |
| 2. Build the env | Stand up a runnable environment for the repo. | `reference_test.py --run` (venv for Python/pytest) |
| 3. Reference test | Run the tests **pre-fix** and **post-fix** to find the signals. | `reference_test.py --run` |
| 4. Keep good tasks | A task is kept only if some test goes **fail → pass** (`FAIL_TO_PASS`). No such test → discard. | `reference_test.py` |

The two test signals are:

- **`FAIL_TO_PASS`** — tests that **fail before** the fix and **pass after**. This
  is the *issue-resolution* signal: it proves the fix did something.
- **`PASS_TO_PASS`** — tests that **pass before and after**. This is the
  *regression* signal: it proves the fix did not break anything.

## Output schema

`collect_prs.py` writes one JSON object per line with the SWE-bench fields:

```json
{
  "instance_id": "pytest-dev__pytest-14639",
  "repo": "pytest-dev/pytest",
  "base_commit": "e6d8374...",
  "patch": "<code diff (the gold fix)>",
  "test_patch": "<test diff (the hidden grader)>",
  "problem_statement": "<linked issue title + body>",
  "FAIL_TO_PASS": ["testing/test_assertion.py::...::test_..."],
  "PASS_TO_PASS": ["...171 tests..."],
  "base_commit": "...",
  "environment_setup_commit": "..."
}
```

Fields prefixed with `_` (`_pr_url`, `_linked_issues`, `_num_files`) are provenance,
not part of the schema.

## How to run it

```bash
# Stage 1: collect candidate instances (no test results yet).
uv run python evals/dataset/collect_prs.py --repo pytest-dev/pytest --max-scan 80 --max-per-repo 5

# cheap correctness check: do the reconstructed patches apply to base_commit?
uv run python evals/dataset/reference_test.py            # apply-only, any repo

# Stages 2-4: build envs, run pre/post tests, fill FAIL_TO_PASS / PASS_TO_PASS.
uv run python evals/dataset/reference_test.py --run --timeout 400   # Python/pytest only
```

Collect from several repos at once with a file:

```bash
printf "pytest-dev/pytest\npsf/requests\n" > repos.txt
uv run python evals/dataset/collect_prs.py --repos-file repos.txt
```

The validated dataset lands in `evals/dataset/out/instances_validated.jsonl`. A
small committed sample is in `sample_validated.jsonl` (4 real pytest tasks).

## Scaling up

The collector is rate-limit aware. To go past a handful of tasks:

- **Set a token.** `export GITHUB_TOKEN=...` raises the anonymous limit from
  **60 → 5000 requests/hour** and unlocks more endpoints. Without it the run
  stops cleanly and saves what it has.
- **Pick high-throughput repos** whose contributors write `Fixes #N` in the PR
  body. We can only see issue links written in the PR text; links made through
  GitHub's UI need the GraphQL API (token + a `closingIssuesReferences` query),
  which is a natural next addition.

## Known limits (the hard part)

Step 2, *building the environment*, is the fragile part — and the reason
SWE-bench ships **per-repo Docker images** rather than auto-installing.

- `reference_test.py --run` only handles **Python/pytest** projects. It makes a
  `uv` venv, installs the project plus its test extras (`dev` / `test` /
  `testing`), and runs the test files named in the `test_patch`.
- It papers over two common self-hosting snags (a shallow checkout has no git
  tag, so it pretends a high version for `setuptools-scm` and overrides
  `minversion`; it force-loads the `pytester` plugin).
- Other languages, or repos with system dependencies, need the
  Docker-image-per-repo approach from the recipe. That is the next big piece.

## Not yet built

- **Problem-statement rewrite** (the recipe's last step): rewrite the issue text
  into a short, vague description so the model must rely on the hidden tests.
  This needs an LLM pass and is intentionally left out for now.
- **GraphQL issue linkage** and **Docker env generation** as noted above.
