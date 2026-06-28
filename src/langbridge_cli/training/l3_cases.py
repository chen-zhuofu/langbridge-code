"""Build L3 reviewer eval cases from task specs.

Each spec expands into two cases with test-based ground truth:
  - gold: the real fix (should pass hidden tests)
  - no_fix: base + hidden tests only (should still fail)
"""

from langbridge_cli.training import bench


def l3_cases_from_specs(specs, grade):
    """Return reviewer cases ready for runner.eval_l3 + review_fn.

    `grade(task_id, code_diff)` must apply hidden tests and return
    `{resolved: bool, ...}` (same grader as L4 eval).
    """
    cases = []
    for spec in specs:
        task_id = spec["task_id"]
        gold = bench.split_diff(spec.get("gold_code_patch", ""))
        shared = {
            "task_id": task_id,
            "base_commit": spec["base_commit"],
            "problem_statement": spec["problem_statement"],
            "test_patch": spec.get("test_patch", ""),
            "test_files": spec.get("test_files", []),
            "repo": spec.get("repo"),
        }
        cases.append({
            **shared,
            "case": "gold",
            "diff": gold,
            "gt_pass": bool(grade(task_id, gold).get("resolved")),
        })
        cases.append({
            **shared,
            "case": "no_fix",
            "diff": "",
            "gt_pass": bool(grade(task_id, "").get("resolved")),
        })
    return cases
