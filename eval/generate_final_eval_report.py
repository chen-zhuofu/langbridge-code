"""Generate the canonical final eval report as JSON and Chinese Markdown.

This command only reads benchmark artifacts. It fails before writing reports
unless LangBridge retained15+rerun3, Pro30+Scale official grade, and the one
Interactive result pass all integrity checks. A Pro run whose recorded prompt
protocol is missing or mismatched is retained as diagnostic evidence but
explicitly excluded from benchmark scoring.

    uv run python eval/generate_final_eval_report.py \
      --generated-at 2026-07-30T20:00:00Z
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from util.pro_prompt import format_problem_statement, prompt_protocol, prompt_sha256


ROOT = Path(__file__).resolve().parents[1]
LB_RETAINED = ROOT / "artifacts/evals/20260729-043022"
LB_RERUN = ROOT / "artifacts/evals/lb402-rerun/20260730-151102"
LB_SPECS = ROOT / "eval/data/langbridge-bench/specs"
LB_DROP = ROOT / "eval/data/langbridge-bench/drop/drop.json"
PRO_RUN = ROOT / "eval/out/pro30-deepseek-v4-20260729-final"
PRO_PREDICTIONS_JSONL = PRO_RUN / "predictions.jsonl"
PRO_GRADE = PRO_RUN / "official-grade/eval_results.json"
INTERACTIVE_REPORT = ROOT / "interactive-bench/eval/out/20260729-081939/report.json"
INTERACTIVE_DROP = ROOT / "interactive-bench/data/drop/drop.json"
INTERACTIVE_SPEC = (
    ROOT
    / "interactive-bench/data/specs/adhishthite__anthropic-clio-impl__0280ad20.json"
)
EVAL_CONFIG = ROOT / "eval/config.json"
INTERACTIVE_CONFIG = ROOT / "interactive-bench/config.json"
RUNNER_GUARD = ROOT / "eval/run_agent.py"
RUNNER_GUARD_TEST = ROOT / "tests/unit/test_run_agent_wrap.py"
PROVENANCE = ROOT / "artifacts/evals/final-eval-provenance.json"
OUTPUT_JSON = ROOT / "artifacts/evals/final-eval-report.json"
OUTPUT_MD = ROOT / "artifacts/evals/final-eval-report.md"


class ReportError(RuntimeError):
    """A canonical input is missing, invalid, or internally inconsistent."""


@dataclass(frozen=True)
class Inputs:
    langbridge_retained: Path = LB_RETAINED
    langbridge_rerun: Path = LB_RERUN
    langbridge_specs: Path = LB_SPECS
    langbridge_drop: Path = LB_DROP
    pro_run: Path = PRO_RUN
    pro_predictions_jsonl: Path = PRO_PREDICTIONS_JSONL
    pro_grade: Path = PRO_GRADE
    interactive_report: Path = INTERACTIVE_REPORT
    interactive_drop: Path = INTERACTIVE_DROP
    interactive_spec: Path = INTERACTIVE_SPEC
    eval_config: Path = EVAL_CONFIG
    interactive_config: Path = INTERACTIVE_CONFIG
    runner_guard: Path = RUNNER_GUARD
    runner_guard_test: Path = RUNNER_GUARD_TEST
    provenance: Path = PROVENANCE


class Context:
    """Read inputs once while collecting hashes and named passing assertions."""

    def __init__(self) -> None:
        self.sources: dict[Path, dict[str, Any]] = {}
        self.checks: list[dict[str, Any]] = []

    @staticmethod
    def display(path: Path) -> str:
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(ROOT))
        except ValueError:
            return str(resolved)

    def _bytes(self, path: Path) -> bytes:
        path = path.resolve()
        if not path.is_file():
            raise ReportError(f"required input is missing: {self.display(path)}")
        data = path.read_bytes()
        self.sources[path] = {
            "path": self.display(path),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        return data

    def json(self, path: Path, kind: type = dict) -> Any:
        try:
            value = json.loads(self._bytes(path).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReportError(f"invalid JSON in {self.display(path)}: {error}") from error
        self.require(isinstance(value, kind), f"{self.display(path)} must be {kind.__name__}")
        return value

    def text(self, path: Path) -> str:
        try:
            return self._bytes(path).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ReportError(f"{self.display(path)} is not UTF-8") from error

    def sha256(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved not in self.sources:
            self._bytes(resolved)
        return str(self.sources[resolved]["sha256"])

    @staticmethod
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ReportError(message)

    def check(self, name: str, observed: Any, expected: Any) -> None:
        if observed != expected:
            raise ReportError(
                f"integrity check failed ({name}): "
                f"observed={observed!r}, expected={expected!r}"
            )
        self.checks.append(
            {"name": name, "passed": True, "observed": observed, "expected": expected}
        )

    def manifest(self) -> list[dict[str, Any]]:
        return [
            self.sources[path]
            for path in sorted(self.sources, key=lambda path: self.display(path))
        ]


def _last_json(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _provider_failure_type(text: str) -> str | None:
    """Classify only wrapper-level provider failures, not matching prose."""
    payload = _last_json(text)
    prefixes = ("Request failed:", "API daily token quota is exhausted")
    failures = [
        candidate
        for field in ("error", "report")
        if (candidate := str(payload.get(field) or "").strip()).startswith(prefixes)
    ]
    if not failures:
        return None
    for failure in failures:
        folded = failure.casefold()
        if (
            failure.startswith("API daily token quota is exhausted")
            or "error code: 402" in folded
            or "insufficient balance" in folded
        ):
            return "quota"
    if any(
        "incomplete chunked read" in failure.casefold()
        or "peer closed connection" in failure.casefold()
        for failure in failures
    ):
        return "transport"
    return "other"


def _quota_failure(text: str) -> bool:
    return _provider_failure_type(text) == "quota"


def _jsonl_objects(ctx: Context, path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(ctx.text(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReportError(
                f"invalid JSONL in {ctx.display(path)} at line {line_number}: {error}"
            ) from error
        ctx.require(
            isinstance(row, dict),
            f"{ctx.display(path)} line {line_number} must be object",
        )
        rows.append(row)
    return rows


def _declared_path(ctx: Context, value: Any, label: str) -> Path:
    ctx.require(isinstance(value, str) and bool(value.strip()), f"{label} path is missing")
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _declared_ids(
    ctx: Context, value: Any, name: str, expected_count: int
) -> list[str]:
    ctx.require(isinstance(value, list), f"{name} must be an array")
    ids = [str(item or "").strip() for item in value]
    ctx.check(
        name,
        [len(ids), len(set(ids)), all(ids)],
        [expected_count, expected_count, True],
    )
    return sorted(ids)


def _read_provenance(ctx: Context, inputs: Inputs) -> dict[str, Any]:
    record = ctx.json(inputs.provenance)
    ctx.check(
        "provenance_identity",
        [record.get("schema_version"), record.get("record_type")],
        ["1.0", "post_run_operator_audit"],
    )
    cleanup = record.get("cleanup")
    sut = record.get("sut_configuration")
    pro = record.get("pro")
    ctx.require(
        isinstance(cleanup, dict) and isinstance(sut, dict) and isinstance(pro, dict),
        "provenance sections are missing",
    )
    deleted = cleanup.get("deleted_quota_polluted")
    retained = cleanup.get("retained_valid")
    retries = cleanup.get("additional_pro_transport_retries")
    ctx.require(
        isinstance(deleted, dict)
        and isinstance(retained, dict)
        and isinstance(retries, dict),
        "provenance cleanup sections are missing",
    )
    lb_deleted = _declared_ids(
        ctx,
        deleted.get("langbridge_task_ids"),
        "provenance_langbridge_deleted_ids",
        3,
    )
    pro_deleted = _declared_ids(
        ctx,
        deleted.get("pro_task_ids"),
        "provenance_pro_deleted_ids",
        23,
    )
    pro_retained = _declared_ids(
        ctx,
        retained.get("pro_task_ids"),
        "provenance_pro_retained_ids",
        7,
    )
    retry_ids = _declared_ids(
        ctx,
        retries.get("task_ids"),
        "provenance_pro_transport_retry_ids",
        2,
    )
    ctx.check(
        "provenance_cleanup_counts",
        [
            deleted.get("total"),
            retained.get("total"),
            retained.get("langbridge_count"),
            retained.get("interactive_count"),
        ],
        [26, 23, 15, 1],
    )
    ctx.check(
        "provenance_pro_cleanup_partition",
        [
            bool(set(pro_deleted) & set(pro_retained)),
            set(retry_ids) <= set(pro_deleted),
        ],
        [False, True],
    )
    evidence_basis = str(cleanup.get("evidence_basis") or "").strip()
    cause = str(cleanup.get("cause") or "").strip()
    retry_reason = str(retries.get("reason") or "").strip()
    ctx.require(
        bool(evidence_basis and cause and retry_reason),
        "provenance cleanup explanation is incomplete",
    )
    return {
        "cleanup": {
            "evidence_basis": evidence_basis,
            "cause": cause,
            "deleted_total": deleted["total"],
            "langbridge_deleted_ids": lb_deleted,
            "pro_deleted_ids": pro_deleted,
            "retained_total": retained["total"],
            "langbridge_retained_count": retained["langbridge_count"],
            "pro_retained_ids": pro_retained,
            "interactive_retained_count": retained["interactive_count"],
            "pro_transport_retry_ids": retry_ids,
            "pro_transport_retry_reason": retry_reason,
        },
        "sut_configuration": sut,
        "pro": pro,
    }


def _valid_timeout(row: dict[str, Any]) -> bool:
    return bool(row.get("timed_out")) and str(row.get("error") or "").startswith(
        "agent timed out after "
    )


def _failed(outcomes: Any) -> list[str]:
    if not isinstance(outcomes, dict):
        return []
    return sorted(k for k, v in outcomes.items() if str(v).upper() != "PASSED")


def _langbridge(ctx: Context, inputs: Inputs) -> dict[str, Any]:
    retained = sorted(inputs.langbridge_retained.glob("*/summary.json"))
    rerun = sorted(inputs.langbridge_rerun.glob("*/summary.json"))
    ctx.check("langbridge_retained_count", len(retained), 15)
    ctx.check("langbridge_rerun_count", len(rerun), 3)

    specs: dict[str, dict[str, Any]] = {}
    for path in sorted(inputs.langbridge_specs.glob("*.json")):
        spec = ctx.json(path)
        task_id = str(spec.get("task_id") or "")
        ctx.require(task_id and task_id not in specs, f"bad/duplicate spec: {task_id}")
        specs[task_id] = spec
    ctx.check("langbridge_active_spec_count", len(specs), 18)

    rows: dict[str, tuple[dict[str, Any], Path, str]] = {}
    for paths, phase in ((retained, "retained"), (rerun, "rerun_after_402")):
        for path in paths:
            row = ctx.json(path)
            task_id = str(row.get("task_id") or "")
            ctx.require(task_id == path.parent.name, f"task/path mismatch: {path}")
            ctx.require(task_id not in rows, f"duplicate LangBridge result: {task_id}")
            rows[task_id] = (row, path, phase)

    rerun_ids = {task_id for task_id, (_, _, phase) in rows.items() if phase != "retained"}
    ctx.check("langbridge_ids_match_specs", sorted(rows), sorted(specs))

    tasks, quota = [], []
    for task_id in sorted(rows):
        row, summary_path, phase = rows[task_id]
        spec = specs[task_id]
        ctx.require(row.get("repo") == spec.get("repo"), f"{task_id}: repo mismatch")
        if _quota_failure(ctx.text(summary_path.parent / "agent.log")):
            quota.append(task_id)

        timeout, error = bool(row.get("timed_out")), str(row.get("error") or "")
        diff_chars = int(row.get("diff_chars") or 0)
        grade_path, grade = summary_path.parent / "grade.json", None
        if timeout:
            ctx.require(_valid_timeout(row), f"{task_id}: malformed timeout")
            ctx.require(
                row.get("gt_pass") is False and row.get("grade_status") == "not_graded",
                f"{task_id}: invalid timeout grading",
            )
            status, passed, f2p_passed, f2p_total = "timeout", False, None, None
            regressions, static_score, failed_tests, grade_display = [], None, [], None
        else:
            ctx.require(not error, f"{task_id}: infrastructure error remains: {error}")
            ctx.require(row.get("agent_returncode") == 0, f"{task_id}: bad returncode")
            ctx.require(row.get("grade_status") == "graded", f"{task_id}: not graded")
            grade = ctx.json(grade_path)
            ctx.require(grade.get("status") == "graded", f"{task_id}: bad grade status")
            ctx.require(type(grade.get("resolved")) is bool, f"{task_id}: bad resolved")
            ctx.require(grade["resolved"] is row.get("gt_pass"), f"{task_id}: grade mismatch")
            passed = grade["resolved"]
            status = "passed" if passed else "failed_tests"
            f2p_passed, f2p_total = int(grade.get("f2p_passed") or 0), int(
                grade.get("f2p_total") or 0
            )
            ctx.require(0 <= f2p_passed <= f2p_total, f"{task_id}: bad F2P counts")
            regressions = grade.get("regressions") or []
            ctx.require(isinstance(regressions, list), f"{task_id}: bad regressions")
            outcomes = grade.get("outcomes")
            fail_to_pass, pass_to_pass = (
                spec.get("fail_to_pass"),
                spec.get("pass_to_pass"),
            )
            ctx.require(
                isinstance(outcomes, dict)
                and isinstance(fail_to_pass, list)
                and isinstance(pass_to_pass, list),
                f"{task_id}: missing spec/grader test lists",
            )
            expected_f2p_passed = sum(
                str(outcomes.get(test)).upper() == "PASSED" for test in fail_to_pass
            )
            ctx.require(
                [f2p_passed, f2p_total]
                == [expected_f2p_passed, len(fail_to_pass)],
                f"{task_id}: F2P counts do not match spec outcomes",
            )
            expected_regressions = sorted(
                test
                for test in pass_to_pass
                if str(outcomes.get(test)).upper() != "PASSED"
            )
            ctx.require(
                sorted(str(test) for test in regressions) == expected_regressions,
                f"{task_id}: regressions do not match spec outcomes",
            )
            static = grade.get("static_analysis") or {}
            ctx.require(isinstance(static, dict), f"{task_id}: bad static analysis")
            static_score, failed_tests = static.get("score"), _failed(outcomes)
            grade_display = ctx.display(grade_path)

        candidate_path = summary_path.parent / "candidate.diff"
        if candidate_path.is_file():
            candidate = ctx.text(candidate_path)
            ctx.require(
                len(candidate) == diff_chars,
                f"{task_id}: candidate.diff length does not match summary",
            )
            candidate_display = ctx.display(candidate_path)
            candidate_sha256 = ctx.sha256(candidate_path)
        else:
            ctx.require(
                timeout and diff_chars == 0,
                f"{task_id}: candidate.diff is missing",
            )
            candidate_display = candidate_sha256 = None
        tasks.append(
            {
                "task_id": task_id,
                "repo": row["repo"],
                "source_phase": phase,
                "status": status,
                "passed": passed,
                "grade_status": row["grade_status"],
                "has_patch": diff_chars > 0,
                "diff_chars": diff_chars,
                "duration_s": float(row.get("duration_s") or 0),
                "agent_returncode": row.get("agent_returncode"),
                "timed_out": timeout,
                "error": error,
                "f2p_passed": f2p_passed,
                "f2p_total": f2p_total,
                "regressions": regressions,
                "static_analysis_score": static_score,
                "failed_tests": failed_tests,
                "summary_path": ctx.display(summary_path),
                "grade_path": grade_display,
                "candidate_diff_path": candidate_display,
                "candidate_diff_sha256": candidate_sha256,
            }
        )

    ctx.check("langbridge_no_quota_failures", quota, [])
    passed = sum(task["passed"] for task in tasks)
    patched = sum(task["has_patch"] for task in tasks)
    return {
        "benchmark": "LangBridge Bench",
        "denominator_policy": "全部 18 个 active specs 进入分母；合法 timeout 计失败。",
        "sources": {
            "retained": ctx.display(inputs.langbridge_retained),
            "rerun_after_402": ctx.display(inputs.langbridge_rerun),
            "active_specs": ctx.display(inputs.langbridge_specs),
        },
        "totals": {
            "tasks": 18,
            "passed": passed,
            "failed": 18 - passed,
            "timeouts": sum(task["timed_out"] for task in tasks),
            "infra_errors": 0,
            "with_patch": patched,
            "zero_patch": 18 - patched,
        },
        "pass_rate": passed / 18,
        "retained_ids": sorted(set(rows) - rerun_ids),
        "rerun_ids": sorted(rerun_ids),
        "tasks": tasks,
    }


def _resource_recheck(
    ctx: Context,
    inputs: Inputs,
    pro_meta: dict[str, Any],
    summary_ids: list[str],
    official: dict[str, bool],
) -> dict[str, Any]:
    resource = pro_meta.get("resource_recheck")
    ctx.require(isinstance(resource, dict), "Pro resource recheck provenance is missing")
    evidence = resource.get("evidence")
    controls = resource.get("resource_controls")
    provisional = resource.get("provisional_raw")
    prior_attempts = resource.get("prior_invalid_attempts")
    caveat = resource.get("nodebb_51_single_worker_caveat")
    ctx.require(
        all(
            isinstance(section, dict)
            for section in (
                evidence,
                controls,
                provisional,
                prior_attempts,
                caveat,
            )
        ),
        "Pro resource recheck sections are incomplete",
    )
    ctx.check(
        "pro_resource_recheck_status",
        resource.get("status"),
        "canonicalized_after_resource_safe_recheck",
    )
    recheck_ids = _declared_ids(
        ctx,
        resource.get("invalid_task_ids"),
        "pro_resource_recheck_declared_ids",
        9,
    )
    ctx.check(
        "pro_resource_recheck_ids_in_run",
        sorted(set(recheck_ids) & set(summary_ids)),
        recheck_ids,
    )

    paths = {
        "ids": _declared_path(ctx, evidence.get("ids_path"), "resource recheck IDs"),
        "predictions": _declared_path(
            ctx,
            evidence.get("predictions_path"),
            "resource recheck predictions",
        ),
        "script": _declared_path(
            ctx, evidence.get("script_path"), "resource-safe grader script"
        ),
        "eval_results": _declared_path(
            ctx, evidence.get("eval_results_path"), "resource recheck results"
        ),
    }
    ids_text = ctx.text(paths["ids"])
    predictions = ctx.json(paths["predictions"], list)
    ctx.text(paths["script"])
    recheck_results = ctx.json(paths["eval_results"])
    for name, path in paths.items():
        ctx.check(
            f"pro_resource_recheck_{name}_sha256",
            ctx.sha256(path),
            evidence.get(f"{name}_sha256"),
        )

    ids_file = [line.strip() for line in ids_text.splitlines() if line.strip()]
    ctx.check(
        "pro_resource_recheck_ids_file",
        [len(ids_file), len(set(ids_file)), sorted(ids_file)],
        [9, 9, recheck_ids],
    )
    prediction_ids = [
        str(row.get("instance_id") or "")
        for row in predictions
        if isinstance(row, dict)
    ]
    ctx.check(
        "pro_resource_recheck_prediction_ids",
        [len(predictions), len(set(prediction_ids)), sorted(prediction_ids)],
        [9, 9, recheck_ids],
    )
    ctx.check("pro_resource_recheck_result_ids", sorted(recheck_results), recheck_ids)
    ctx.check(
        "pro_resource_recheck_result_values",
        sorted(
            task_id
            for task_id, value in recheck_results.items()
            if type(value) is not bool
        ),
        [],
    )
    ctx.check(
        "pro_resource_recheck_matches_canonical",
        {task_id: official[task_id] for task_id in recheck_ids},
        recheck_results,
    )

    segfault_logs = []
    canonical_log_paths = []
    for task_id in recheck_ids:
        for suffix in ("stdout", "stderr"):
            path = (
                inputs.pro_grade.parent
                / task_id
                / f"langbridge-l4_{suffix}.log"
            )
            text = ctx.text(path)
            canonical_log_paths.append(ctx.display(path))
            folded = text.casefold()
            if "segmentation fault" in folded or "segfault" in folded:
                segfault_logs.append(ctx.display(path))
    ctx.check("pro_resource_recheck_no_toolchain_segfaults", segfault_logs, [])

    reason = str(provisional.get("invalid_reason") or "").strip()
    ctx.check(
        "pro_resource_recheck_provisional_disposition",
        [
            provisional.get("resolved"),
            provisional.get("total"),
            provisional.get("disposition"),
            all(token in reason.casefold() for token in ("go", "gcc", "vet", "seg")),
        ],
        [5, 30, "discarded_as_invalid", True],
    )
    ctx.check(
        "pro_resource_recheck_controls",
        [
            controls.get("rosetta_enabled"),
            controls.get("docker_nano_cpus"),
            controls.get("goflags"),
            controls.get("gomaxprocs"),
        ],
        [True, 2_000_000_000, "-p=1", 2],
    )
    ctx.check(
        "pro_resource_recheck_prior_attempts_deleted",
        prior_attempts.get("permanently_deleted"),
        True,
    )
    script_delta = str(resource.get("script_delta") or "").strip()
    prior_note = str(prior_attempts.get("note") or "").strip()
    ctx.require(
        bool(script_delta and prior_note),
        "Pro resource recheck audit explanation is incomplete",
    )

    nodebb_id = str(caveat.get("instance_id") or "")
    caveat_note = str(caveat.get("note") or "").strip()
    ctx.check(
        "pro_resource_recheck_nodebb_caveat",
        [
            caveat.get("passed"),
            caveat.get("failed"),
            caveat.get("canonical_result"),
            official.get(nodebb_id),
            nodebb_id in set(summary_ids),
            bool(caveat_note),
            "maxretries" in caveat_note.casefold(),
            "econnrefused" in caveat_note.casefold(),
        ],
        [166, 11, False, False, True, True, True, True],
    )
    return {
        "status": str(resource.get("status") or ""),
        "invalid_task_ids": recheck_ids,
        "provisional_raw": {
            "resolved": provisional["resolved"],
            "total": provisional["total"],
            "disposition": provisional["disposition"],
            "invalid_reason": reason,
        },
        "evidence": {
            name: {
                "path": ctx.display(path),
                "sha256": ctx.sha256(path),
            }
            for name, path in paths.items()
        },
        "resource_controls": controls,
        "script_delta": script_delta,
        "prior_invalid_attempts": {
            "permanently_deleted": True,
            "note": prior_note,
        },
        "results": recheck_results,
        "canonical_log_paths": canonical_log_paths,
        "nodebb_51_single_worker_caveat": {
            "instance_id": nodebb_id,
            "passed": 166,
            "failed": 11,
            "canonical_result": False,
            "note": caveat_note,
        },
    }


def _pro(
    ctx: Context, inputs: Inputs, provenance: dict[str, Any]
) -> dict[str, Any]:
    summary_path, prediction_path = (
        inputs.pro_run / "run_summary.json",
        inputs.pro_run / "predictions-pro.json",
    )
    pro_meta = provenance["pro"]
    grader_meta = pro_meta.get("official_grader")
    ctx.require(isinstance(grader_meta, dict), "official grader provenance is missing")
    raw_sample_path = _declared_path(
        ctx, pro_meta.get("raw_sample_path"), "Pro raw sample"
    )
    grader_script_path = _declared_path(
        ctx, grader_meta.get("script_path"), "Pro official grader script"
    )
    run, predictions, jsonl_predictions, official = (
        ctx.json(summary_path),
        ctx.json(prediction_path, list),
        _jsonl_objects(ctx, inputs.pro_predictions_jsonl),
        ctx.json(inputs.pro_grade),
    )
    raw_sample = _jsonl_objects(ctx, raw_sample_path)
    ctx.text(grader_script_path)
    ctx.check(
        "pro_raw_sample_sha256",
        ctx.sha256(raw_sample_path),
        pro_meta.get("raw_sample_sha256"),
    )
    ctx.check(
        "pro_official_grader_script_sha256",
        ctx.sha256(grader_script_path),
        grader_meta.get("script_sha256"),
    )
    ctx.check(
        "pro_provenance_identity",
        [pro_meta.get("dataset"), pro_meta.get("split"), pro_meta.get("selection")],
        ["ScaleAI/SWE-bench_Pro", "test", "first 30 rows"],
    )
    ctx.require(
        all(
            str(grader_meta.get(field) or "").strip()
            for field in ("repository", "commit", "mode")
        ),
        "official grader provenance is incomplete",
    )
    summaries = run.get("summaries")
    ctx.require(isinstance(summaries, list), "Pro summaries must be an array")
    ctx.check(
        "pro_run_identity",
        [run.get("difficulty"), run.get("dataset"), run.get("split")],
        ["pro", "ScaleAI/SWE-bench_Pro", "test"],
    )
    ctx.check("pro_summary_count", [run.get("completed"), len(summaries)], [30, 30])

    summary_ids = [str(row.get("instance_id") or "") for row in summaries]
    prediction_ids = [str(row.get("instance_id") or "") for row in predictions]
    ctx.check(
        "pro_ordered_unique_predictions",
        [len(set(summary_ids)), len(predictions), prediction_ids == summary_ids],
        [30, 30, True],
    )
    jsonl_ids = [str(row.get("instance_id") or "") for row in jsonl_predictions]
    ctx.check(
        "pro_jsonl_ordered_unique_predictions",
        [len(jsonl_predictions), len(set(jsonl_ids)), jsonl_ids == summary_ids],
        [30, 30, True],
    )
    raw_ids = [str(row.get("instance_id") or "") for row in raw_sample]
    ctx.check(
        "pro_raw_sample_ordered_unique",
        [len(raw_sample), len(set(raw_ids)), raw_ids == summary_ids],
        [30, 30, True],
    )
    raw_by_id = {str(row.get("instance_id") or ""): row for row in raw_sample}
    expected_prompt_protocol = prompt_protocol("pro")
    recorded_prompt_protocol = run.get("prompt_protocol")
    missing_prompt_hash_ids = []
    mismatched_prompt_hash_ids = []
    missing_prompt_artifact_ids = []
    mismatched_prompt_artifact_ids = []
    summary_by_id = {
        str(row.get("instance_id") or ""): row for row in summaries
    }
    for instance_id in summary_ids:
        expected_hash = prompt_sha256(raw_by_id[instance_id], difficulty="pro")
        recorded_hash = summary_by_id[instance_id].get("prompt_sha256")
        if not str(recorded_hash or "").strip():
            missing_prompt_hash_ids.append(instance_id)
        elif recorded_hash != expected_hash:
            mismatched_prompt_hash_ids.append(instance_id)
        prompt_artifact = (
            inputs.pro_run / "artifacts" / instance_id / "agent_prompt.txt"
        )
        if not prompt_artifact.is_file():
            missing_prompt_artifact_ids.append(instance_id)
        elif ctx.sha256(prompt_artifact) != expected_hash:
            mismatched_prompt_artifact_ids.append(instance_id)
    prompt_protocol_valid = (
        recorded_prompt_protocol == expected_prompt_protocol
        and not missing_prompt_hash_ids
        and not mismatched_prompt_hash_ids
        and not missing_prompt_artifact_ids
        and not mismatched_prompt_artifact_ids
    )
    if prompt_protocol_valid:
        prompt_protocol_reason = (
            "run-level protocol metadata plus all 30 per-task prompt artifacts and "
            "hashes match the official Scale problem/requirements/interface template."
        )
    else:
        prompt_protocol_reason = (
            "The legacy inference run cannot be certified against the official "
            "Scale prompt protocol: it lacks matching protocol metadata and/or "
            "per-task prompt artifacts/hashes. The runner used for this run supplied only "
            "problem_statement, omitting requirements and interface; its grader "
            "output is retained for diagnosis but excluded from benchmark scoring."
        )
    prediction_format_mismatches = [
        {
            "index": index,
            "jsonl_instance_id": jsonl_row.get("instance_id"),
            "json_instance_id": json_row.get("instance_id"),
            "patch_matches": jsonl_row.get("model_patch") == json_row.get("patch"),
        }
        for index, (jsonl_row, json_row) in enumerate(
            zip(jsonl_predictions, predictions, strict=True)
        )
        if (
            jsonl_row.get("instance_id") != json_row.get("instance_id")
            or jsonl_row.get("model_patch") != json_row.get("patch")
        )
    ]
    ctx.check("pro_prediction_formats_match", prediction_format_mismatches, [])
    ctx.check("pro_official_ids", sorted(official), sorted(summary_ids))
    ctx.check(
        "pro_official_values",
        sorted(key for key, value in official.items() if type(value) is not bool),
        [],
    )
    resource_recheck = _resource_recheck(
        ctx, inputs, pro_meta, summary_ids, official
    )
    retained_ids = provenance["cleanup"]["pro_retained_ids"]
    deleted_ids = provenance["cleanup"]["pro_deleted_ids"]
    retained_set = set(retained_ids)
    ctx.check(
        "pro_cleanup_partition_matches_run",
        [
            sorted(set(summary_ids) & retained_set),
            sorted(set(summary_ids) - retained_set),
        ],
        [retained_ids, deleted_ids],
    )

    prediction_by_id = {
        row.get("instance_id"): row for row in predictions if isinstance(row, dict)
    }
    ctx.require(len(prediction_by_id) == 30, "malformed/duplicate Pro predictions")
    tasks, infra, quota = [], [], []
    for row in summaries:
        instance_id = str(row["instance_id"])
        prediction = prediction_by_id[instance_id]
        patch = prediction.get("patch")
        ctx.require(isinstance(patch, str), f"{instance_id}: patch is not a string")
        has_patch = bool(patch.strip())
        ctx.require(row.get("has_patch") is has_patch, f"{instance_id}: has_patch mismatch")
        ctx.require(row.get("patch_chars") == len(patch), f"{instance_id}: patch size mismatch")

        timeout, error = bool(row.get("timed_out")), str(row.get("error") or "")
        if timeout:
            ctx.require(_valid_timeout(row), f"{instance_id}: malformed timeout")
            if prompt_protocol_valid:
                ctx.require(
                    row.get("timeout_patch_salvage") == "succeeded",
                    f"{instance_id}: timeout patch was not safely salvaged",
                )
            agent_status = "timeout"
        elif error:
            infra.append({"instance_id": instance_id, "error": error})
            agent_status = "infra_error"
        else:
            ctx.require(row.get("returncode") == 0, f"{instance_id}: bad returncode")
            agent_status = "completed"

        stdout = inputs.pro_run / "artifacts" / instance_id / "agent_stdout.txt"
        if _quota_failure(ctx.text(stdout)):
            quota.append(instance_id)
        tasks.append(
            {
                "instance_id": instance_id,
                "repo": str(row.get("repo") or ""),
                "source_phase": (
                    "retained" if instance_id in retained_set else "rerun_after_402"
                ),
                "resolved": official[instance_id],
                "agent_status": agent_status,
                "has_patch": has_patch,
                "patch_chars": len(patch),
                "duration_s": float(row.get("duration_s") or 0),
                "returncode": row.get("returncode"),
                "timed_out": timeout,
                "error": error,
            }
        )

    ctx.check("pro_no_infrastructure_errors", infra, [])
    ctx.check("pro_no_quota_failures", quota, [])
    rerun_ids = sorted(set(summary_ids) - retained_set)
    ctx.check("pro_rerun_ids", rerun_ids, deleted_ids)

    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        by_repo[task["repo"]].append(task)
    repo_breakdown = []
    for repo, rows in sorted(by_repo.items()):
        resolved = sum(row["resolved"] for row in rows)
        repo_breakdown.append(
            {
                "repo": repo,
                "tasks": len(rows),
                "resolved": resolved,
                "pass_rate": resolved / len(rows),
            }
        )
    resolved, patched = sum(t["resolved"] for t in tasks), sum(t["has_patch"] for t in tasks)
    return {
        "benchmark": "SWE-bench Pro",
        "dataset": "ScaleAI/SWE-bench_Pro",
        "selection": "test split 前 30 题",
        "grader": "Scale SWE-bench Pro official harness",
        "official_grader": {
            "repository": grader_meta["repository"],
            "commit": grader_meta["commit"],
            "script_path": ctx.display(grader_script_path),
            "script_sha256": ctx.sha256(grader_script_path),
            "mode": grader_meta["mode"],
        },
        "resource_recheck": resource_recheck,
        "prompt_protocol": {
            "status": "valid" if prompt_protocol_valid else "protocol_invalid",
            "score_eligible": prompt_protocol_valid,
            "expected": expected_prompt_protocol,
            "recorded": recorded_prompt_protocol,
            "missing_prompt_hash_ids": missing_prompt_hash_ids,
            "mismatched_prompt_hash_ids": mismatched_prompt_hash_ids,
            "missing_prompt_artifact_ids": missing_prompt_artifact_ids,
            "mismatched_prompt_artifact_ids": mismatched_prompt_artifact_ids,
            "reason": prompt_protocol_reason,
        },
        "denominator_policy": (
            "official eval_results.json 的全部 30 个 boolean 结果。"
            if prompt_protocol_valid
            else "Inference prompt protocol 无效；30 题全部排除出可报告分母，"
            "official grader 输出仅作为诊断证据保留。"
        ),
        "sources": {
            "raw_sample": ctx.display(raw_sample_path),
            "run_summary": ctx.display(summary_path),
            "predictions": ctx.display(prediction_path),
            "predictions_jsonl": ctx.display(inputs.pro_predictions_jsonl),
            "official_grade": ctx.display(inputs.pro_grade),
            "official_grader_script": ctx.display(grader_script_path),
        },
        "totals": {
            "tasks": 30,
            "score_eligible_tasks": 30 if prompt_protocol_valid else 0,
            "resolved": resolved if prompt_protocol_valid else None,
            "unresolved": 30 - resolved if prompt_protocol_valid else None,
            "diagnostic_resolved": resolved,
            "diagnostic_unresolved": 30 - resolved,
            "timeouts": sum(t["timed_out"] for t in tasks),
            "infra_errors": 0,
            "with_patch": patched,
            "zero_patch": 30 - patched,
        },
        "pass_rate": resolved / 30 if prompt_protocol_valid else None,
        "diagnostic_pass_rate": resolved / 30,
        "repo_breakdown": repo_breakdown,
        "retained_ids": retained_ids,
        "rerun_ids": rerun_ids,
        "transport_retry_ids": provenance["cleanup"]["pro_transport_retry_ids"],
        "tasks": tasks,
    }


def _interactive(ctx: Context, inputs: Inputs) -> dict[str, Any]:
    report = ctx.json(inputs.interactive_report)
    results = report.get("results")
    ctx.require(isinstance(results, list), "Interactive results must be an array")
    ctx.check(
        "interactive_run_identity",
        [
            report.get("n"),
            report.get("n_scorable"),
            report.get("n_voided"),
            len(results),
            report.get("stub"),
        ],
        [1, 1, 0, 1, False],
    )
    result = results[0]
    task_id = str(result.get("task_id") or "")
    ctx.require(task_id and result.get("valid") is True, "Interactive result is invalid")
    ctx.require(result.get("sim_error") is None, f"{task_id}: simulator error")
    ctx.require(result.get("tests_graded") is True, f"{task_id}: tests not graded")
    expected_pass = bool(result.get("valid")) and bool(result.get("tests_passed"))
    ctx.require(result.get("pass") is expected_pass, f"{task_id}: bad pass flag")
    grade, outcomes = result.get("grade"), (result.get("grade") or {}).get("outcomes")
    ctx.require(isinstance(grade, dict) and isinstance(outcomes, dict), f"{task_id}: bad grade")
    f2p_passed, f2p_total = int(grade.get("f2p_passed") or 0), int(
        grade.get("f2p_total") or 0
    )
    ctx.require(
        [f2p_passed, f2p_total]
        == [sum(str(v).upper() == "PASSED" for v in outcomes.values()), len(outcomes)],
        f"{task_id}: F2P/outcome mismatch",
    )
    passed = int(expected_pass)
    ctx.check(
        "interactive_top_level_counts",
        [report.get("n_passed"), report.get("pass_rate")],
        [passed, float(passed)],
    )
    task = {
        "task_id": task_id,
        "source_phase": "retained",
        "valid": True,
        "sim_error": None,
        "pass": expected_pass,
        "tests_graded": True,
        "tests_passed": bool(result.get("tests_passed")),
        "f2p_passed": f2p_passed,
        "f2p_total": f2p_total,
        "test_outcomes": outcomes,
        "failed_tests": _failed(outcomes),
        "intent_coverage": result.get("intent_coverage"),
        "intent_coverage_mode": result.get("intent_coverage_mode"),
        "intent_coverage_notes": result.get("intent_coverage_notes"),
        "intents_covered": result.get("intents_covered") or [],
        "intents_missing": result.get("intents_missing") or [],
        "coverage_model": result.get("coverage_model"),
        "user_input_count": result.get("user_input_count"),
        "interventions": result.get("interventions"),
        "elapsed_sec": result.get("elapsed_sec"),
        "runtime_ratio": result.get("runtime_ratio"),
        "input_ratio": result.get("input_ratio"),
        "stop_reason": result.get("stop_reason"),
    }
    return {
        "benchmark": "Interactive Bench",
        "pass_criterion": "pass = valid && tests_passed；intent coverage 当前仅作参考。",
        "source": ctx.display(inputs.interactive_report),
        "totals": {"tasks": 1, "scorable": 1, "voided": 0, "passed": passed, "failed": 1 - passed},
        "pass_rate": float(passed),
        "config": report.get("config") or {},
        "tasks": [task],
    }


def _quality(
    ctx: Context, inputs: Inputs, langbridge: dict[str, Any], interactive: dict[str, Any]
) -> dict[str, Any]:
    lb_drop, int_drop = ctx.json(inputs.langbridge_drop), ctx.json(inputs.interactive_drop)
    lb_rows, int_rows = lb_drop.get("dropped"), int_drop.get("dropped")
    ctx.require(isinstance(lb_rows, list) and isinstance(int_rows, list), "bad drop manifest")
    lb_drops = []
    for row in lb_rows:
        ctx.require(isinstance(row, dict), "bad LangBridge drop row")
        drop = {
            "task_id": str(row.get("task_id") or "").strip(),
            "reason": str(row.get("reason") or "").strip(),
            "dropped_at": str(row.get("dropped_at") or "").strip(),
        }
        ctx.require(all(drop.values()), "incomplete LangBridge drop row")
        lb_drops.append(drop)
    int_drops = []
    for row in int_rows:
        ctx.require(isinstance(row, dict), "bad Interactive drop row")
        drop = {
            "task_id": str(row.get("task_id") or "").strip(),
            "reason": str(row.get("reason") or "").strip(),
        }
        dropped_at = str(row.get("dropped_at") or "").strip()
        if dropped_at:
            drop["dropped_at"] = dropped_at
        ctx.require(drop["task_id"] and drop["reason"], "incomplete Interactive drop row")
        int_drops.append(drop)
    lb_drops.sort(key=lambda row: row["task_id"])
    int_drops.sort(key=lambda row: row["task_id"])
    lb_ids = [row["task_id"] for row in lb_drops]
    int_ids = [row["task_id"] for row in int_drops]
    ctx.check("langbridge_quality_drop_manifest", [len(lb_ids), len(set(lb_ids))], [13, 13])
    ctx.check("interactive_quality_drop_manifest", [len(int_ids), len(set(int_ids))], [13, 13])
    current_lb_drops = [
        row for row in lb_drops if row["dropped_at"].startswith("2026-07-29")
    ]
    current_lb_ids = [row["task_id"] for row in current_lb_drops]
    prior_lb_drops = [
        row for row in lb_drops if row["task_id"] not in set(current_lb_ids)
    ]
    prior_lb_ids = [row["task_id"] for row in prior_lb_drops]
    ctx.check("langbridge_current_review_drop_count", len(current_lb_drops), 11)
    ctx.check("langbridge_prior_drop_count", len(prior_lb_drops), 2)
    active_lb = {task["task_id"] for task in langbridge["tasks"]}
    ctx.check("langbridge_drops_excluded", sorted(set(lb_ids) & active_lb), [])

    lb_rewrites = []
    for task_id in sorted(active_lb):
        spec_path = inputs.langbridge_specs / f"{task_id}.json"
        spec = ctx.json(spec_path)
        reason = str(spec.get("rewrite_reason") or "").strip()
        if not reason:
            continue
        rewrite = {
            "task_id": task_id,
            "rewrite_reason": reason,
            "spec_path": ctx.display(spec_path),
        }
        source = str(spec.get("problem_statement_source") or "").strip()
        if source:
            rewrite["problem_statement_source"] = source
        lb_rewrites.append(rewrite)
    ctx.check(
        "langbridge_active_rewrites",
        [
            len(lb_rewrites),
            len({row["task_id"] for row in lb_rewrites}),
            all(row["rewrite_reason"] for row in lb_rewrites),
        ],
        [11, 11, True],
    )

    int_spec = ctx.json(inputs.interactive_spec)
    int_meta = int_spec.get("metadata")
    ctx.require(isinstance(int_meta, dict), "Interactive active spec metadata is missing")
    int_reason = str(int_meta.get("human_rewrite_reason") or "").strip()
    int_id = str(int_spec.get("task_id") or "")
    ctx.check(
        "interactive_active_rewrite",
        [
            int_id,
            int_id == interactive["tasks"][0]["task_id"],
            int_id not in set(int_ids),
            bool(int_reason),
            int_meta.get("baseline_comparable_after_rewrite"),
        ],
        [interactive["tasks"][0]["task_id"], True, True, True, False],
    )
    return {
        "langbridge": {
            "current_human_review": {
                "reviewed_count": 29,
                "active_count": 18,
                "dropped_count": 11,
                "dropped_ids": current_lb_ids,
                "dropped": current_lb_drops,
            },
            "cumulative_dropped_count": 13,
            "cumulative_dropped_ids": lb_ids,
            "cumulative_dropped": lb_drops,
            "prior_dropped_count": 2,
            "prior_dropped_ids": prior_lb_ids,
            "prior_dropped": prior_lb_drops,
            "drop_manifest": ctx.display(inputs.langbridge_drop),
            "drop_manifest_sha256": ctx.sources[inputs.langbridge_drop.resolve()]["sha256"],
            "active_rewrite_count": len(lb_rewrites),
            "active_rewrites": lb_rewrites,
        },
        "interactive": {
            "reviewed_count": 14,
            "active_count": 1,
            "dropped_count": 13,
            "dropped_ids": int_ids,
            "dropped": int_drops,
            "drop_manifest": ctx.display(inputs.interactive_drop),
            "drop_manifest_sha256": ctx.sources[inputs.interactive_drop.resolve()]["sha256"],
            "active_rewrite": {
                "task_id": int_id,
                "human_rewrite_reason": int_reason,
                "baseline_comparable_after_rewrite": False,
                "spec_path": ctx.display(inputs.interactive_spec),
            },
        },
    }


def _models(
    ctx: Context,
    inputs: Inputs,
    interactive: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    eval_cfg, int_cfg = ctx.json(inputs.eval_config), ctx.json(inputs.interactive_config)
    agent, int_agent, int_models = (
        eval_cfg.get("agent") or {},
        int_cfg.get("agent") or {},
        int_cfg.get("interactive") or {},
    )
    observed = [
        agent.get("provider"),
        agent.get("model"),
        (agent.get("agent_models") or {}).get("explorer"),
        int_agent.get("provider"),
        int_agent.get("model"),
        (int_agent.get("agent_models") or {}).get("explorer"),
        int_models.get("sim_model"),
        int_models.get("coverage_model"),
    ]
    expected = [
        "deepseek",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "deepseek",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "gpt-5.6",
        "claude-fable-5",
    ]
    ctx.check("model_configuration", observed, expected)
    sut_provenance = provenance["sut_configuration"]
    ctx.check(
        "provenance_sut_models",
        [
            sut_provenance.get("provider"),
            sut_provenance.get("default_model"),
            (sut_provenance.get("role_overrides") or {}).get("explorer"),
        ],
        observed[:3],
    )
    ctx.check(
        "provenance_eval_config_path",
        sut_provenance.get("eval_config_path"),
        ctx.display(inputs.eval_config),
    )
    ctx.check(
        "provenance_interactive_config_path",
        sut_provenance.get("interactive_config_path"),
        ctx.display(inputs.interactive_config),
    )
    ctx.check(
        "provenance_eval_config_sha256",
        sut_provenance.get("eval_config_sha256"),
        ctx.sha256(inputs.eval_config),
    )
    ctx.check(
        "provenance_interactive_config_sha256",
        sut_provenance.get("interactive_config_sha256"),
        ctx.sha256(inputs.interactive_config),
    )
    embedded = interactive.get("config") or {}
    embedded_agent, embedded_int = embedded.get("agent") or {}, embedded.get("interactive") or {}
    ctx.check(
        "interactive_embedded_models",
        [
            embedded_agent.get("provider"),
            embedded_agent.get("model"),
            (embedded_agent.get("agent_models") or {}).get("explorer"),
            embedded_int.get("sim_model"),
            embedded_int.get("coverage_model"),
        ],
        ["deepseek", "deepseek-v4-pro", "deepseek-v4-flash", "gpt-5.6", "claude-fable-5"],
    )
    evidence_basis = str(sut_provenance.get("evidence_basis") or "").strip()
    limitation = str(sut_provenance.get("limitation") or "").strip()
    ctx.require(
        bool(evidence_basis and limitation),
        "SUT attribution provenance is incomplete",
    )
    return {
        "sut": {
            "provider": "deepseek",
            "default_model": "deepseek-v4-pro",
            "role_overrides": {"explorer": "deepseek-v4-flash"},
            "note": "除 explorer 外，未覆盖的 agent roles 继承 default_model。",
        },
        "non_sut_models": {
            "interactive_user_simulator": "gpt-5.6",
            "interactive_coverage_judge": "claude-fable-5",
        },
        "config_files": [ctx.display(inputs.eval_config), ctx.display(inputs.interactive_config)],
        "attribution_basis": {
            "langbridge": {
                "kind": "launcher_and_config_reconstruction",
                "detail": evidence_basis,
            },
            "pro": {
                "kind": "launcher_and_config_reconstruction",
                "detail": evidence_basis,
            },
            "interactive": {
                "kind": "embedded_runtime_config",
                "detail": (
                    "Interactive report.json embeds the runtime model configuration; "
                    "it is cross-checked against interactive-bench/config.json."
                ),
            },
        },
        "limitation": limitation,
    }


def _remediation(
    ctx: Context,
    provenance: dict[str, Any],
    langbridge: dict[str, Any],
    pro: dict[str, Any],
    interactive: dict[str, Any],
    runner_guard: dict[str, Any],
) -> dict[str, Any]:
    cleanup = provenance["cleanup"]
    lb_rerun_ids = sorted(
        task["task_id"]
        for task in langbridge["tasks"]
        if task["source_phase"] == "rerun_after_402"
    )
    pro_rerun_ids = sorted(
        task["instance_id"]
        for task in pro["tasks"]
        if task["source_phase"] == "rerun_after_402"
    )
    pro_retained_ids = sorted(
        task["instance_id"]
        for task in pro["tasks"]
        if task["source_phase"] == "retained"
    )
    ctx.check(
        "cleanup_langbridge_deleted_ids_match_report",
        cleanup["langbridge_deleted_ids"],
        lb_rerun_ids,
    )
    ctx.check(
        "cleanup_pro_deleted_ids_match_report",
        cleanup["pro_deleted_ids"],
        pro_rerun_ids,
    )
    ctx.check(
        "cleanup_pro_retained_ids_match_report",
        cleanup["pro_retained_ids"],
        pro_retained_ids,
    )
    ctx.check(
        "cleanup_pro_retry_ids_match_report",
        cleanup["pro_transport_retry_ids"],
        sorted(pro["transport_retry_ids"]),
    )
    ctx.check(
        "cleanup_retained_counts_match_report",
        [
            len(langbridge["retained_ids"]),
            len(pro_retained_ids),
            len(interactive["tasks"]),
        ],
        [
            cleanup["langbridge_retained_count"],
            len(cleanup["pro_retained_ids"]),
            cleanup["interactive_retained_count"],
        ],
    )
    return {
        "cause": cleanup["cause"],
        "evidence_basis": cleanup["evidence_basis"],
        "policy": (
            "删除 quota 影响的无效结果，仅复用确认有效的结果；"
            "API request failure 是 infrastructure error，不能计作零补丁失败。"
        ),
        "deleted_quota_polluted": {
            "total": cleanup["deleted_total"],
            "langbridge": len(cleanup["langbridge_deleted_ids"]),
            "pro": len(cleanup["pro_deleted_ids"]),
            "langbridge_task_ids": cleanup["langbridge_deleted_ids"],
            "pro_task_ids": cleanup["pro_deleted_ids"],
        },
        "retained_valid": {
            "total": cleanup["retained_total"],
            "langbridge": cleanup["langbridge_retained_count"],
            "pro": len(cleanup["pro_retained_ids"]),
            "interactive": cleanup["interactive_retained_count"],
            "pro_task_ids": cleanup["pro_retained_ids"],
            "scope": (
                "This classification only means the artifacts were not polluted "
                "by HTTP 402. It does not override the later Pro prompt-protocol "
                "invalidation."
            ),
        },
        "unique_tasks_rerun_after_402": {
            "langbridge": len(lb_rerun_ids),
            "pro": len(pro_rerun_ids),
        },
        "additional_infrastructure_retries": {
            "pro": len(cleanup["pro_transport_retry_ids"]),
            "reason": cleanup["pro_transport_retry_reason"],
            "task_ids": cleanup["pro_transport_retry_ids"],
            "policy": "通过 resume 定点覆盖；不计入 quota 删除数。",
        },
        "runner_guard": runner_guard,
        "pro_prompt_protocol": pro["prompt_protocol"],
        "langbridge_rerun_ids": lb_rerun_ids,
        "pro_retained_ids": pro_retained_ids,
        "pro_rerun_ids": pro_rerun_ids,
        "composite_note": (
            f"LangBridge=retained{len(langbridge['retained_ids'])}"
            f"+rerun{len(lb_rerun_ids)}；"
            f"Pro=retained{len(pro_retained_ids)}+rerun{len(pro_rerun_ids)}；"
            f"Interactive 复用 {len(interactive['tasks'])} 个未受 402 影响且可评分的结果。"
            "本段只描述 402 清理来源，不代表 Pro 具备计分资格。"
        ),
    }


def _runner_guard(ctx: Context, inputs: Inputs) -> dict[str, Any]:
    implementation, tests = ctx.text(inputs.runner_guard), ctx.text(inputs.runner_guard_test)
    required_implementation = [
        "def provider_failure_from_report",
        'text.startswith("Request failed:")',
        'text.startswith("API daily token quota is exhausted")',
        '"error": provider_failure',
        "return 1 if provider_failure else 0",
    ]
    required_tests = [
        "test_provider_failure_from_report_detects_formatted_api_error",
        "test_provider_failure_from_report_ignores_normal_agent_text",
    ]
    ctx.check(
        "runner_provider_failure_guard",
        [token for token in required_implementation if token not in implementation],
        [],
    )
    ctx.check(
        "runner_provider_failure_guard_tests",
        [token for token in required_tests if token not in tests],
        [],
    )
    return {
        "behavior": (
            "eval/run_agent.py 将已格式化为开头 `Request failed:` 或 "
            "`API daily token quota is exhausted` 的 provider failure 提升为 "
            "top-level error，并以非零状态退出，避免伪装成正常零补丁结果。"
        ),
        "detection_scope": [
            "report 以 `Request failed:` 开头",
            "report 以 `API daily token quota is exhausted` 开头",
        ],
        "limitation": (
            "这是对当前已知格式的前缀保护，不声称覆盖所有 provider 或未来错误措辞。"
        ),
        "implementation_path": ctx.display(inputs.runner_guard),
        "test_path": ctx.display(inputs.runner_guard_test),
    }


def _timestamp(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ReportError(f"invalid --generated-at: {value}") from error
    if parsed.tzinfo is None:
        raise ReportError("--generated-at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_report(inputs: Inputs, generated_at: str | None = None) -> dict[str, Any]:
    ctx = Context()
    provenance = _read_provenance(ctx, inputs)
    langbridge = _langbridge(ctx, inputs)
    pro = _pro(ctx, inputs, provenance)
    interactive = _interactive(ctx, inputs)
    quality = _quality(ctx, inputs, langbridge, interactive)
    models = _models(ctx, inputs, interactive, provenance)
    runner_guard = _runner_guard(ctx, inputs)
    remediation = _remediation(
        ctx, provenance, langbridge, pro, interactive, runner_guard
    )
    manifest = ctx.manifest()
    return {
        "schema_version": "1.0",
        "generated_at_utc": _timestamp(generated_at),
        "status": (
            "complete"
            if pro["prompt_protocol"]["score_eligible"]
            else "complete_with_protocol_invalid_pro"
        ),
        "models": models,
        "remediation": remediation,
        "task_quality_disposition": quality,
        "benchmarks": {"langbridge": langbridge, "pro": pro, "interactive": interactive},
        "integrity": {
            "passed": True,
            "check_count": len(ctx.checks),
            "checks": ctx.checks,
            "source_file_count": len(manifest),
        },
        "artifacts": {
            "canonical_inputs": {
                name: ctx.display(getattr(inputs, name))
                for name in inputs.__dataclass_fields__
            },
            "source_files": manifest,
        },
    }


def _esc(value: Any) -> str:
    return "—" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def _sec(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1f}s"


def _link(path: str) -> str:
    target = Path(path) if Path(path).is_absolute() else ROOT / path
    return f"[{_esc(path)}](<{target}>)"


def render_markdown(report: dict[str, Any]) -> str:
    """Render only from the canonical JSON object so both formats stay in sync."""
    lb, pro, inter = (
        report["benchmarks"]["langbridge"],
        report["benchmarks"]["pro"],
        report["benchmarks"]["interactive"],
    )
    models, fix, quality = report["models"], report["remediation"], report["task_quality_disposition"]
    recheck = pro["resource_recheck"]
    pro_protocol = pro["prompt_protocol"]
    pro_overview = (
        f"| SWE-bench Pro official | {pro['totals']['resolved']} | 30 | "
        f"{_pct(pro['pass_rate'])} | {pro['totals']['timeouts']} | 0 |"
        if pro_protocol["score_eligible"]
        else (
            "| SWE-bench Pro | INVALID (prompt protocol) | 0/30 eligible | — | "
            f"{pro['totals']['timeouts']} | 0 |"
        )
    )
    out = [
        "# LangBridge 最终评测报告",
        "",
        f"- 生成时间（UTC）：`{report['generated_at_utc']}`",
        f"- Schema：`{report['schema_version']}`",
        (
            "- 状态：完整性校验通过"
            if report["status"] == "complete"
            else "- 状态：完整性校验通过；旧 Pro inference protocol 无效，已排除计分"
        ),
        "",
        "## 总览",
        "",
        "| Benchmark | 通过 | 总数/可评分 | 通过率 | Timeout | Infra error |",
        "|---|---:|---:|---:|---:|---:|",
        f"| LangBridge | {lb['totals']['passed']} | 18 | {_pct(lb['pass_rate'])} | {lb['totals']['timeouts']} | 0 |",
        pro_overview,
        f"| Interactive | {inter['totals']['passed']} | 1 | {_pct(inter['pass_rate'])} | — | 0 voided |",
        "",
        "> 三套 benchmark 的任务、grader 和分母不同，不计算跨 benchmark 平均分。",
        *(
            [
                "",
                "> **Pro 结果作废：** 旧运行没有按 Scale 官方模板向 agent 提供 "
                "`requirements` 和 `interface`，因此历史 grader 输出不能作为 "
                "SWE-bench Pro 成绩。修复后的 runner 会记录协议版本和逐题 prompt SHA256。",
            ]
            if not pro_protocol["score_eligible"]
            else []
        ),
        "",
        "## 模型配置",
        "",
        "| 角色 | Provider | Model | SUT |",
        "|---|---|---|---|",
        f"| 默认 agent roles | {models['sut']['provider']} | {models['sut']['default_model']} | 是 |",
        f"| explorer | {models['sut']['provider']} | {models['sut']['role_overrides']['explorer']} | 是 |",
        f"| Interactive simulator | — | {models['non_sut_models']['interactive_user_simulator']} | 否 |",
        f"| Interactive coverage judge | — | {models['non_sut_models']['interactive_coverage_judge']} | 否 |",
        "",
        models["sut"]["note"],
        (
            "- LangBridge / Pro attribution_basis："
            f"`{models['attribution_basis']['langbridge']['kind']}`"
            "（launcher + hashed config 重建）。"
        ),
        (
            "- Interactive attribution_basis："
            f"`{models['attribution_basis']['interactive']['kind']}`"
            "（report.json embedded runtime config）。"
        ),
        f"- Attribution evidence：{models['attribution_basis']['langbridge']['detail']}",
        f"- Limitation：{models['limitation']}",
        "",
        "## 无效结果清理与重跑",
        "",
        f"- 402 清理根因：`{fix['cause']}`。",
        (
            "- 独立的 Pro 作废原因：旧 inference runner 漏传 "
            "`requirements` / `interface`；这与 quota 和 official grader "
            "资源故障是三类不同问题。"
        ),
        (
            f"- 永久删除 quota-polluted 结果共 "
            f"{fix['deleted_quota_polluted']['total']} 个：LangBridge "
            f"{fix['deleted_quota_polluted']['langbridge']} + Pro "
            f"{fix['deleted_quota_polluted']['pro']}。"
        ),
        (
            f"- 未受 402 污染的结果共 {fix['retained_valid']['total']} 个：LangBridge "
            f"{fix['retained_valid']['langbridge']} + Pro "
            f"{fix['retained_valid']['pro']} + Interactive "
            f"{fix['retained_valid']['interactive']}；这不代表 Pro protocol 有效。"
        ),
        (
            f"- LangBridge：{fix['unique_tasks_rerun_after_402']['langbridge']} "
            "个 quota 任务各重跑一次。"
        ),
        (
            f"- Pro：{fix['unique_tasks_rerun_after_402']['pro']} 个 quota 任务重跑；"
            f"其中另有 {fix['additional_infrastructure_retries']['pro']} 个 "
            f"{fix['additional_infrastructure_retries']['reason']}，"
            "通过 resume 定点覆盖，不计入 quota 删除数。"
        ),
        (
            f"- Interactive：复用未受 402 影响的 "
            f"{fix['retained_valid']['interactive']} 题。"
        ),
        f"- Cleanup evidence：{fix['evidence_basis']}",
        f"- 口径：{fix['policy']}",
        f"- 合并说明：{fix['composite_note']}",
        (
            f"- Runner guard：{fix['runner_guard']['behavior']} "
            f"实现 {_link(fix['runner_guard']['implementation_path'])}；"
            f"测试 {_link(fix['runner_guard']['test_path'])}。"
        ),
        f"- 限制：{fix['runner_guard']['limitation']}",
        "",
        "LangBridge 重跑 ID：",
        "",
        *[f"- `{task_id}`" for task_id in fix["langbridge_rerun_ids"]],
        "",
        "## 题目质量处置",
        "",
        (
            "- LangBridge 本轮人肉检查 29 题：18 active + 11 新 drop（2026-07-29）。"
        ),
        (
            f"- LangBridge drop manifest 历史累计 "
            f"{quality['langbridge']['cumulative_dropped_count']} 题"
            f"（本轮 11 + 较早 pytest drop 2）；"
            f"manifest {_link(quality['langbridge']['drop_manifest'])}；"
            f"SHA256 `{quality['langbridge']['drop_manifest_sha256']}`。"
        ),
        "",
        "### LangBridge 本轮新增 drop（11）",
        "",
        "| Task | Reason |",
        "|---|---|",
        *[
            f"| `{_esc(row['task_id'])}` | {_esc(row['reason'])} |"
            for row in quality["langbridge"]["current_human_review"]["dropped"]
        ],
        "",
        "### LangBridge 历史 drop（2）",
        "",
        "| Task | Reason |",
        "|---|---|",
        *[
            f"| `{_esc(row['task_id'])}` | {_esc(row['reason'])} |"
            for row in quality["langbridge"]["prior_dropped"]
        ],
        "",
        "### LangBridge active rewrites（11）",
        "",
        "| Task | Problem statement source | Rewrite reason（原文） |",
        "|---|---|---|",
        *[
            (
                f"| `{_esc(row['task_id'])}` | "
                f"{_esc(row.get('problem_statement_source'))} | "
                f"{_esc(row['rewrite_reason'])} |"
            )
            for row in quality["langbridge"]["active_rewrites"]
        ],
        "",
        (
            f"- Interactive：共检查 14 题，人工 drop "
            f"{quality['interactive']['dropped_count']} + active/rewrite 1；"
            f"manifest {_link(quality['interactive']['drop_manifest'])}；"
            f"SHA256 `{quality['interactive']['drop_manifest_sha256']}`。"
        ),
        "",
        "### Interactive drop（13）",
        "",
        "| Task | Reason |",
        "|---|---|",
        *[
            f"| `{_esc(row['task_id'])}` | {_esc(row['reason'])} |"
            for row in quality["interactive"]["dropped"]
        ],
        "",
        f"- Active rewrite：`{quality['interactive']['active_rewrite']['task_id']}`。",
        f"  - human_rewrite_reason（原文）：{_esc(quality['interactive']['active_rewrite']['human_rewrite_reason'])}",
        "  - baseline_comparable_after_rewrite：`false`。",
        "",
        "## LangBridge Bench",
        "",
        f"**{lb['totals']['passed']}/18 ({_pct(lb['pass_rate'])})**；{lb['denominator_policy']}",
        "",
        "| Task | Source | 结果 | Patch | F2P | Static | 耗时 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for task in lb["tasks"]:
        f2p = "—" if task["f2p_total"] is None else f"{task['f2p_passed']}/{task['f2p_total']}"
        out.append(
            f"| `{_esc(task['task_id'])}` | {task['source_phase']} | {task['status']} | "
            f"{task['diff_chars']} | {f2p} | {_esc(task['static_analysis_score'])} | {_sec(task['duration_s'])} |"
        )
    out += ["", "### LangBridge 未通过明细", ""]
    for task in (task for task in lb["tasks"] if not task["passed"]):
        detail = task["error"] if task["timed_out"] else ", ".join(task["failed_tests"])
        out.append(f"- `{task['task_id']}`：{_esc(detail or '未产生通过 grader 的实现')}")

    out += [
        "",
        (
            "## SWE-bench Pro（Scale official）"
            if pro_protocol["score_eligible"]
            else "## SWE-bench Pro（历史运行：prompt protocol invalid）"
        ),
        "",
        (
            f"**{pro['totals']['resolved']}/30 ({_pct(pro['pass_rate'])})**；"
            f"{pro['denominator_policy']}"
            if pro_protocol["score_eligible"]
            else (
                "**INVALID (prompt protocol)，不计分。** "
                f"历史诊断输出为 {pro['totals']['diagnostic_resolved']}/30 "
                f"({_pct(pro['diagnostic_pass_rate'])})，但不能与官方成绩比较；"
                f"{pro['denominator_policy']}"
            )
        ),
        f"- Prompt protocol status：`{pro_protocol['status']}`",
        f"- Reason：{pro_protocol['reason']}",
        f"- Expected protocol：`{pro_protocol['expected']['version']}`",
        f"- Recorded protocol：`{_esc(pro_protocol['recorded'])}`",
        (
            f"- Official grader repository："
            f"[{_esc(pro['official_grader']['repository'])}]"
            f"({pro['official_grader']['repository']})"
        ),
        f"- Official grader commit：`{pro['official_grader']['commit']}`",
        (
            f"- Official grader script："
            f"{_link(pro['official_grader']['script_path'])}；SHA256 "
            f"`{pro['official_grader']['script_sha256']}`"
        ),
        f"- Official grader mode：{pro['official_grader']['mode']}",
        "",
        "### Official resource recheck",
        "",
        (
            f"- Provisional raw：{recheck['provisional_raw']['resolved']}/"
            f"{recheck['provisional_raw']['total']}，"
            f"`{recheck['provisional_raw']['disposition']}`；"
            f"{recheck['provisional_raw']['invalid_reason']}"
        ),
        (
            f"- Resource-safe recheck：{sum(recheck['results'].values())}/"
            f"{len(recheck['results'])}；9 个结果已逐题合入 canonical official map。"
        ),
        (
            "- Resource controls：Rosetta enabled；"
            f"`nano_cpus={recheck['resource_controls']['docker_nano_cpus']}`；"
            f"`GOFLAGS={recheck['resource_controls']['goflags']}`；"
            f"`GOMAXPROCS={recheck['resource_controls']['gomaxprocs']}`。"
        ),
        f"- Script delta：{recheck['script_delta']}",
        (
            "- Earlier invalid attempts：permanently deleted；"
            f"{recheck['prior_invalid_attempts']['note']}"
        ),
        *[
            (
                f"- {name} evidence：{_link(item['path'])}；"
                f"SHA256 `{item['sha256']}`"
            )
            for name, item in recheck["evidence"].items()
        ],
        (
            "- NodeBB51 single-worker caveat："
            f"`{recheck['nodebb_51_single_worker_caveat']['instance_id']}` "
            f"精确复现 {recheck['nodebb_51_single_worker_caveat']['passed']} PASSED / "
            f"{recheck['nodebb_51_single_worker_caveat']['failed']} FAILED，"
            "但 canonical official 结果保留 `false`；"
            f"{recheck['nodebb_51_single_worker_caveat']['note']}"
        ),
        "",
        "| Resource-rechecked instance | Canonical official |",
        "|---|---|",
        *[
            f"| `{_esc(task_id)}` | {'PASS' if result else 'FAIL'} |"
            for task_id, result in sorted(recheck["results"].items())
        ],
        "",
        "### Repo 分布",
        "",
        "| Repo | 通过 | 题数 | 通过率 |",
        "|---|---:|---:|---:|",
    ]
    for row in pro["repo_breakdown"]:
        out.append(f"| {_esc(row['repo'])} | {row['resolved']} | {row['tasks']} | {_pct(row['pass_rate'])} |")
    out += [
        "",
        "### Pro 逐题",
        "",
        "| Instance | Repo | Source | Official | Agent | Patch | 耗时 |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for task in pro["tasks"]:
        out.append(
            f"| `{_esc(task['instance_id'])}` | {_esc(task['repo'])} | {task['source_phase']} | "
            f"{'PASS' if task['resolved'] else 'FAIL'} | {task['agent_status']} | "
            f"{task['patch_chars']} | {_sec(task['duration_s'])} |"
        )

    out += [
        "",
        "## Interactive Bench",
        "",
        f"**{inter['totals']['passed']}/1 ({_pct(inter['pass_rate'])})**；{inter['pass_criterion']}",
        "",
    ]
    for task in inter["tasks"]:
        out += [
            f"### `{task['task_id']}`",
            "",
            f"- 结果：{'PASS' if task['pass'] else 'FAIL'}；测试 {task['f2p_passed']}/{task['f2p_total']}",
            f"- Intent coverage：{_pct(task['intent_coverage'])}（{_esc(task['intent_coverage_mode'])}）",
            f"- 用户输入：{_esc(task['user_input_count'])}；interventions：{_esc(task['interventions'])}",
            f"- 耗时：{_sec(task['elapsed_sec'])}；runtime ratio：{_esc(task['runtime_ratio'])}",
            f"- Stop reason：{_esc(task['stop_reason'])}",
            "",
            "| Test | Outcome |",
            "|---|---|",
            *[f"| `{_esc(name)}` | {_esc(status)} |" for name, status in sorted(task["test_outcomes"].items())],
            "",
            f"Coverage judge 备注：{_esc(task['intent_coverage_notes'])}",
        ]

    out += [
        "",
        "## 完整性与 artifacts",
        "",
        f"{report['integrity']['check_count']} 项断言全部通过；"
        f"{report['integrity']['source_file_count']} 个输入文件记录 SHA256。",
        "",
        "| 检查 | 结果 |",
        "|---|---|",
        *[f"| `{_esc(c['name'])}` | PASS |" for c in report["integrity"]["checks"]],
        "",
        "Canonical inputs：",
        "",
        *[f"- {name}：{_link(path)}" for name, path in report["artifacts"]["canonical_inputs"].items()],
        "",
        "<details>",
        "<summary>全部输入文件 SHA256</summary>",
        "",
        "| 文件 | Bytes | SHA256 |",
        "|---|---:|---|",
        *[
            f"| {_link(src['path'])} | {src['bytes']} | `{src['sha256']}` |"
            for src in report["artifacts"]["source_files"]
        ],
        "",
        "</details>",
        "",
    ]
    return "\n".join(out)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_reports(
    inputs: Inputs,
    output_json: Path,
    output_markdown: Path,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], str, str]:
    if output_json.resolve() == output_markdown.resolve():
        raise ReportError("JSON and Markdown outputs must differ")
    report = build_report(inputs, generated_at)
    json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown = render_markdown(report)
    json_sha = hashlib.sha256(json_text.encode()).hexdigest()
    markdown_sha = hashlib.sha256(markdown.encode()).hexdigest()
    _atomic_write(output_json, json_text)
    _atomic_write(output_markdown, markdown)
    return report, json_sha, markdown_sha


def _path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = {
        "langbridge-retained": LB_RETAINED,
        "langbridge-rerun": LB_RERUN,
        "langbridge-specs": LB_SPECS,
        "langbridge-drop": LB_DROP,
        "pro-run": PRO_RUN,
        "pro-predictions-jsonl": PRO_PREDICTIONS_JSONL,
        "pro-grade": PRO_GRADE,
        "interactive-report": INTERACTIVE_REPORT,
        "interactive-drop": INTERACTIVE_DROP,
        "interactive-spec": INTERACTIVE_SPEC,
        "eval-config": EVAL_CONFIG,
        "interactive-config": INTERACTIVE_CONFIG,
        "runner-guard": RUNNER_GUARD,
        "runner-guard-test": RUNNER_GUARD_TEST,
        "provenance": PROVENANCE,
    }
    for flag, default in inputs.items():
        parser.add_argument(f"--{flag}", type=_path, default=default)
    parser.add_argument("--output-json", type=_path, default=OUTPUT_JSON)
    parser.add_argument("--output-markdown", type=_path, default=OUTPUT_MD)
    parser.add_argument(
        "--generated-at",
        help="Timezone-aware ISO-8601 timestamp for byte-reproducible output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    inputs = Inputs(
        **{
            field: getattr(args, field)
            for field in Inputs.__dataclass_fields__
        }
    )
    try:
        report, json_sha, md_sha = write_reports(
            inputs, args.output_json, args.output_markdown, args.generated_at
        )
    except ReportError as error:
        print(f"final eval report validation failed: {error}", file=sys.stderr)
        return 2
    print(
        f"wrote {args.output_json} (sha256={json_sha}, "
        f"checks={report['integrity']['check_count']})"
    )
    print(
        f"wrote {args.output_markdown} (sha256={md_sha}, "
        f"sources={report['integrity']['source_file_count']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
