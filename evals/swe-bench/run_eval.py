"""Generate SWE-bench predictions by running the langbridge CLI (L4 path) on issues.

For each SWE-bench instance this:
  1. shallow-fetches the repo at base_commit into a work dir,
  2. runs the headless CLI on the issue text (writes go to the repo, auto-approved),
  3. captures `git diff` as the model_patch,
  4. writes a predictions.jsonl the official swebench grader can consume.

Grading is a separate step (needs Docker); see evals/swebench/README.md.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from datasets import load_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
MODEL_NAME = "langbridge-l4"

# SWE-bench variants ordered easy -> hard. Pick one with --difficulty.
DATASETS = {
    "lite": "princeton-nlp/SWE-bench_Lite",          # ~300 self-contained tasks (easy)
    "verified": "princeton-nlp/SWE-bench_Verified",  # 500 human-validated tasks (medium)
    "pro": "ScaleAI/SWE-bench_Pro",                   # enterprise long-horizon tasks (hard)
}


def load_instances(dataset_name, split, count, instance_ids=None):
    rows = list(load_dataset(dataset_name, split=split))
    if instance_ids:
        wanted = set(instance_ids)
        return [row for row in rows if row["instance_id"] in wanted]
    if count:
        rows = rows[:count]
    return rows


def run_command(args, cwd=None, env=None, timeout=None, check=True):
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        timeout=timeout,
        check=check,
        capture_output=True,
        text=True,
    )


def setup_repo(instance, repo_dir):
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"

    run_command(["git", "init", "-q"], cwd=repo_dir)
    run_command(["git", "remote", "add", "origin", url], cwd=repo_dir)
    run_command(["git", "fetch", "-q", "--depth", "1", "origin", base_commit], cwd=repo_dir)
    run_command(["git", "checkout", "-q", "FETCH_HEAD"], cwd=repo_dir)


def decode_stream(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def run_agent(repo_dir, problem_statement, artifacts_dir, model, timeout):
    env = os.environ.copy()
    env["LANGBRIDGE_RUNS_DIR"] = str(artifacts_dir / "session-history")
    env["LANGBRIDGE_TODO_LIST_PATH"] = str(artifacts_dir / "todo_list.md")
    env["PYTHONPATH"] = os.pathsep.join([str(SRC_PATH), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    if model:
        env["LANGBRIDGE_MODEL"] = model

    try:
        result = subprocess.run(
            [sys.executable, "-m", "langbridge_cli.headless", problem_statement],
            cwd=repo_dir,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        returncode = result.returncode
        timed_out = False
        stdout, stderr = result.stdout, result.stderr
    except subprocess.TimeoutExpired as expired:
        # On timeout the captured streams come back as bytes even with text=True.
        returncode = None
        timed_out = True
        stdout = decode_stream(expired.stdout)
        stderr = decode_stream(expired.stderr) + "\n[timed out]"

    (artifacts_dir / "agent_stdout.txt").write_text(decode_stream(stdout), encoding="utf-8")
    (artifacts_dir / "agent_stderr.txt").write_text(decode_stream(stderr), encoding="utf-8")
    return returncode, timed_out


def extract_patch(repo_dir):
    run_command(["git", "add", "-A"], cwd=repo_dir)
    result = run_command(["git", "diff", "--cached"], cwd=repo_dir)
    return result.stdout


def evaluate_instance(instance, work_dir, artifacts_root, model, timeout):
    instance_id = instance["instance_id"]
    repo_dir = work_dir / instance_id
    artifacts_dir = artifacts_root / instance_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    error = ""
    patch = ""
    returncode = None
    timed_out = False
    setup_ok = False
    try:
        setup_repo(instance, repo_dir)
        setup_ok = True
    except subprocess.CalledProcessError as failure:
        error = f"setup: {failure.cmd}: {failure.stderr or failure.stdout}"
    except Exception as failure:  # noqa: BLE001 - record any setup failure
        error = f"setup: {failure}"

    if setup_ok:
        try:
            returncode, timed_out = run_agent(
                repo_dir, instance["problem_statement"], artifacts_dir, model, timeout
            )
        except Exception as failure:  # noqa: BLE001 - record but still extract any edits
            error = f"agent: {failure}"
        # Always extract whatever the agent changed, even on timeout/error.
        try:
            patch = extract_patch(repo_dir)
        except Exception as failure:  # noqa: BLE001
            error = error or f"extract: {failure}"

    duration = round(time.time() - started, 1)
    prediction = {
        "instance_id": instance_id,
        "model_name_or_path": MODEL_NAME,
        "model_patch": patch,
    }
    summary = {
        "instance_id": instance_id,
        "repo": instance["repo"],
        "has_patch": bool(patch.strip()),
        "patch_chars": len(patch),
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_s": duration,
        "error": error,
    }
    return prediction, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--difficulty",
        choices=list(DATASETS),
        default="lite",
        help="SWE-bench variant by difficulty (lite=easy, verified=medium, pro=hard).",
    )
    parser.add_argument("--dataset", default=None, help="Explicit HF dataset id; overrides --difficulty.")
    parser.add_argument("--split", default="test")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument(
        "--instance-id",
        action="append",
        dest="instance_ids",
        help="Run only this instance id (repeatable). Overrides --count.",
    )
    parser.add_argument("--model", default=os.environ.get("LANGBRIDGE_MODEL", ""))
    parser.add_argument("--timeout", type=int, default=900, help="Per-instance agent timeout (s).")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "evals" / "swebench" / "out"))
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY") and not (Path.home() / ".langbridge" / "config.json").exists():
        sys.exit(
            "No API key found. Set OPENAI_API_KEY or create ~/.langbridge/config.json "
            "before running the eval (headless runs cannot prompt for a key)."
        )

    dataset = args.dataset or DATASETS[args.difficulty]

    out_dir = Path(args.out)
    work_dir = out_dir / "repos"
    artifacts_root = out_dir / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    instances = load_instances(dataset, args.split, args.count, args.instance_ids)
    print(f"Loaded {len(instances)} instances from {dataset} [{args.split}].")

    predictions = []
    summaries = []
    for index, instance in enumerate(instances, start=1):
        print(f"\n[{index}/{len(instances)}] {instance['instance_id']} ({instance['repo']})")
        prediction, summary = evaluate_instance(instance, work_dir, artifacts_root, args.model, args.timeout)
        predictions.append(prediction)
        summaries.append(summary)
        print(f"  -> patch: {summary['has_patch']} ({summary['patch_chars']} chars), "
              f"{summary['duration_s']}s, timed_out={summary['timed_out']}"
              + (f", error={summary['error']}" if summary["error"] else ""))

    predictions_path = out_dir / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(json.dumps(prediction) + "\n")
    (out_dir / "run_summary.json").write_text(
        json.dumps({"dataset": dataset, "split": args.split, "summaries": summaries}, indent=2),
        encoding="utf-8",
    )

    resolved = sum(1 for summary in summaries if summary["has_patch"])
    print(f"\nWrote {predictions_path}")
    print(f"Produced a patch for {resolved}/{len(summaries)} instances. Grade with evals/swebench/README.md.")


if __name__ == "__main__":
    main()
