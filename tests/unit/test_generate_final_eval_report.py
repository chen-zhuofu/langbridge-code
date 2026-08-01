import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "eval" / "generate_final_eval_report.py"
SPEC = importlib.util.spec_from_file_location("generate_final_eval_report", MODULE_PATH)
reporter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = reporter
SPEC.loader.exec_module(reporter)

LB_RERUN_IDS = [
    "NousResearch__hermes-agent-72320",
    "NousResearch__hermes-agent-73668",
    "NousResearch__hermes-agent-73681",
]
PRO_RETAINED_IDS = [f"instance_retained_{index:02d}" for index in range(7)]


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixtures(tmp_path: Path) -> reporter.Inputs:
    old = tmp_path / "lb-old"
    rerun = tmp_path / "lb-rerun"
    specs = tmp_path / "lb-specs"
    lb_drop = tmp_path / "lb-drop.json"
    pro = tmp_path / "pro"
    pro_grade = pro / "official-grade/eval_results.json"
    interactive_report = tmp_path / "interactive-report.json"
    interactive_drop = tmp_path / "interactive-drop.json"
    interactive_spec = tmp_path / "interactive-spec.json"
    eval_config = tmp_path / "eval-config.json"
    interactive_config = tmp_path / "interactive-config.json"
    provenance = tmp_path / "provenance.json"

    old_ids = ["NousResearch__hermes-agent-72403"] + [
        f"old-task-{index:02d}" for index in range(14)
    ]
    rerun_ids = sorted(LB_RERUN_IDS)
    active_ids = old_ids + rerun_ids
    rewrite_ids = set(active_ids[:11])
    for index, task_id in enumerate(active_ids):
        spec = {
            "task_id": task_id,
            "repo": "owner/repo",
            "fail_to_pass": ["test_case"],
            "pass_to_pass": ["regression_case"],
        }
        if task_id in rewrite_ids:
            spec["rewrite_reason"] = (
                "original LangBridge rewrite reason"
                if task_id == "NousResearch__hermes-agent-72403"
                else f"LangBridge rewrite reason {task_id}"
            )
            spec["problem_statement_source"] = "rewritten"
        _write(specs / f"{task_id}.json", spec)

        run_dir = (old if task_id in old_ids else rerun) / task_id
        passed = index % 2 == 0
        _write(
            run_dir / "summary.json",
            {
                "task_id": task_id,
                "repo": "owner/repo",
                "gt_pass": passed,
                "grade_status": "graded",
                "diff_chars": 10 if passed else 0,
                "duration_s": index + 0.5,
                "timed_out": False,
                "agent_returncode": 0,
                "error": "",
            },
        )
        _write(
            run_dir / "grade.json",
            {
                "resolved": passed,
                "status": "graded",
                "f2p_passed": int(passed),
                "f2p_total": 1,
                "regressions": [],
                "static_analysis": {"score": 1.0 if passed else None},
                "outcomes": {
                    "test_case": "PASSED" if passed else "FAILED",
                    "regression_case": "PASSED",
                },
            },
        )
        _write(run_dir / "agent.log", {"report": "normal completion"})
        (run_dir / "candidate.diff").write_text(
            "x" * (10 if passed else 0),
            encoding="utf-8",
        )

    dropped = []
    for index in range(13):
        dropped.append(
            {
                "task_id": f"lb-dropped-{index:02d}",
                "reason": f"LangBridge drop reason {index:02d}",
                "dropped_at": (
                    "2026-07-29T00:00:00Z"
                    if index < 11
                    else "2026-07-27T00:00:00Z"
                ),
            }
        )
    _write(lb_drop, {"dropped": dropped})

    pro_ids = PRO_RETAINED_IDS + [
        f"instance_rerun_{index:02d}" for index in range(23)
    ]
    summaries, predictions, official, raw_rows = [], [], {}, []
    for index, instance_id in enumerate(pro_ids):
        patch = "diff --git a/a b/a\n+fix\n" if index % 3 == 0 else ""
        raw_row = {
            "instance_id": instance_id,
            "repo": f"repo/{index % 3}",
            "problem_statement": f"problem {index}",
            "requirements": f"requirements {index}",
            "interface": "No new interfaces are introduced.",
        }
        raw_rows.append(raw_row)
        summaries.append(
            {
                "instance_id": instance_id,
                "repo": f"repo/{index % 3}",
                "has_patch": bool(patch),
                "patch_chars": len(patch),
                "returncode": 0,
                "timed_out": False,
                "error": "",
                "duration_s": index + 1,
                "prompt_sha256": reporter.prompt_sha256(
                    raw_row, difficulty="pro"
                ),
            }
        )
        predictions.append(
            {"instance_id": instance_id, "patch": patch, "prefix": "langbridge-l4"}
        )
        official[instance_id] = index % 5 == 0
        _write(
            pro / "artifacts" / instance_id / "agent_stdout.txt",
            {"report": "normal completion"},
        )
        (pro / "artifacts" / instance_id / "agent_prompt.txt").write_text(
            reporter.format_problem_statement(raw_row, difficulty="pro"),
            encoding="utf-8",
        )
    _write(
        pro / "run_summary.json",
        {
            "difficulty": "pro",
            "dataset": "ScaleAI/SWE-bench_Pro",
            "split": "test",
            "prompt_protocol": reporter.prompt_protocol("pro"),
            "completed": 30,
            "summaries": summaries,
        },
    )
    _write(pro / "predictions-pro.json", predictions)
    _write_jsonl(
        pro / "predictions.jsonl",
        [
            {
                "instance_id": row["instance_id"],
                "model_name_or_path": row["prefix"],
                "model_patch": row["patch"],
            }
            for row in predictions
        ],
    )
    _write(pro_grade, official)
    raw_sample = pro / "swebench-pro-first30.jsonl"
    grader_script = pro / "swe_bench_pro_eval.py"
    _write_jsonl(
        raw_sample,
        raw_rows,
    )
    grader_script.write_text("# official grader fixture\n", encoding="utf-8")
    recheck_ids = pro_ids[7:16]
    recheck_ids_path = pro / "official-resource-recheck-ids.txt"
    recheck_predictions_path = pro / "predictions-official-resource-recheck.json"
    resource_script = pro / "swe_bench_pro_eval_resource_safe.py"
    recheck_results_path = pro / "official-recheck-stable/eval_results.json"
    recheck_ids_path.write_text("\n".join(recheck_ids) + "\n", encoding="utf-8")
    _write(
        recheck_predictions_path,
        [row for row in predictions if row["instance_id"] in set(recheck_ids)],
    )
    resource_script.write_text("# resource-safe official grader\n", encoding="utf-8")
    _write(
        recheck_results_path,
        {task_id: official[task_id] for task_id in recheck_ids},
    )
    for task_id in recheck_ids:
        log_dir = pro_grade.parent / task_id
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "langbridge-l4_stdout.log").write_text(
            "resource-safe test completion\n",
            encoding="utf-8",
        )
        (log_dir / "langbridge-l4_stderr.log").write_text("", encoding="utf-8")

    interactive_task = "adhishthite__anthropic-clio-impl__0280ad20"
    embedded_config = {
        "agent": {
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "agent_models": {"explorer": "deepseek-v4-flash"},
        },
        "interactive": {
            "sim_model": "gpt-5.6",
            "coverage_model": "claude-fable-5",
        },
    }
    _write(
        interactive_report,
        {
            "n": 1,
            "n_scorable": 1,
            "n_voided": 0,
            "n_passed": 0,
            "pass_rate": 0.0,
            "stub": False,
            "config": embedded_config,
            "results": [
                {
                    "task_id": interactive_task,
                    "valid": True,
                    "sim_error": None,
                    "tests_graded": True,
                    "tests_passed": False,
                    "pass": False,
                    "grade": {
                        "f2p_passed": 1,
                        "f2p_total": 2,
                        "outcomes": {"test_a": "PASSED", "test_b": "FAILED"},
                    },
                    "intent_coverage": 1.0,
                    "intent_coverage_mode": "llm_judge",
                    "intent_coverage_notes": "coverage note",
                    "intents_covered": ["i1"],
                    "intents_missing": [],
                    "coverage_model": "claude-fable-5",
                    "user_input_count": 1,
                    "interventions": 0,
                    "elapsed_sec": 12.0,
                    "runtime_ratio": 0.5,
                    "input_ratio": 0.1,
                    "stop_reason": "agent_done",
                }
            ],
        },
    )
    _write(
        interactive_drop,
        {
            "dropped": [
                {
                    "task_id": f"interactive-drop-{index}",
                    "reason": f"Interactive drop reason {index}",
                }
                for index in range(13)
            ]
        },
    )
    _write(
        interactive_spec,
        {
            "task_id": interactive_task,
            "metadata": {
                "human_rewrite_reason": "original Interactive rewrite reason",
                "baseline_comparable_after_rewrite": False,
            },
        },
    )
    _write(
        eval_config,
        {
            "agent": {
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "agent_models": {"explorer": "deepseek-v4-flash"},
            }
        },
    )
    _write(interactive_config, embedded_config)
    _write(
        provenance,
        {
            "schema_version": "1.0",
            "record_type": "post_run_operator_audit",
            "cleanup": {
                "evidence_basis": "fixture cleanup evidence",
                "cause": "DeepSeek HTTP 402 Insufficient Balance",
                "deleted_quota_polluted": {
                    "total": 26,
                    "langbridge_task_ids": rerun_ids,
                    "pro_task_ids": pro_ids[7:],
                },
                "retained_valid": {
                    "total": 23,
                    "langbridge_count": 15,
                    "pro_task_ids": PRO_RETAINED_IDS,
                    "interactive_count": 1,
                },
                "additional_pro_transport_retries": {
                    "reason": "incomplete chunked read",
                    "task_ids": pro_ids[7:9],
                },
            },
            "sut_configuration": {
                "evidence_basis": "launcher plus checked-in config reconstruction",
                "provider": "deepseek",
                "default_model": "deepseek-v4-pro",
                "role_overrides": {"explorer": "deepseek-v4-flash"},
                "eval_config_path": str(eval_config),
                "eval_config_sha256": _sha256(eval_config),
                "interactive_config_path": str(interactive_config),
                "interactive_config_sha256": _sha256(interactive_config),
                "limitation": (
                    "LangBridge and Pro use reconstructed launcher/config evidence; "
                    "only Interactive embeds runtime config."
                ),
            },
            "pro": {
                "dataset": "ScaleAI/SWE-bench_Pro",
                "split": "test",
                "selection": "first 30 rows",
                "raw_sample_path": str(raw_sample),
                "raw_sample_sha256": _sha256(raw_sample),
                "official_grader": {
                    "repository": "https://example.test/SWE-bench_Pro-os.git",
                    "commit": "fixture-commit",
                    "script_path": str(grader_script),
                    "script_sha256": _sha256(grader_script),
                    "mode": "fixture Docker mode",
                },
                "resource_recheck": {
                    "status": "canonicalized_after_resource_safe_recheck",
                    "invalid_task_ids": recheck_ids,
                    "provisional_raw": {
                        "resolved": 5,
                        "total": 30,
                        "disposition": "discarded_as_invalid",
                        "invalid_reason": (
                            "Go compile, GCC, and go vet segfaults invalidated "
                            "these infrastructure results."
                        ),
                    },
                    "evidence": {
                        "ids_path": str(recheck_ids_path),
                        "ids_sha256": _sha256(recheck_ids_path),
                        "predictions_path": str(recheck_predictions_path),
                        "predictions_sha256": _sha256(recheck_predictions_path),
                        "script_path": str(resource_script),
                        "script_sha256": _sha256(resource_script),
                        "eval_results_path": str(recheck_results_path),
                        "eval_results_sha256": _sha256(recheck_results_path),
                    },
                    "resource_controls": {
                        "rosetta_enabled": True,
                        "docker_nano_cpus": 2_000_000_000,
                        "goflags": "-p=1",
                        "gomaxprocs": 2,
                    },
                    "script_delta": (
                        "Relative to the official script, only GOFLAGS=-p=1, "
                        "GOMAXPROCS=2, and the 2-CPU Docker limit were added."
                    ),
                    "prior_invalid_attempts": {
                        "permanently_deleted": True,
                        "note": "Earlier invalid resource attempts were deleted.",
                    },
                    "nodebb_51_single_worker_caveat": {
                        "instance_id": pro_ids[1],
                        "passed": 166,
                        "failed": 11,
                        "canonical_result": False,
                        "note": (
                            "The exact single-worker reproduction yielded Redis "
                            "MaxRetries / ECONNREFUSED failures, and the official "
                            "boolean remains false."
                        ),
                    },
                },
            },
        },
    )
    return reporter.Inputs(
        langbridge_retained=old,
        langbridge_rerun=rerun,
        langbridge_specs=specs,
        langbridge_drop=lb_drop,
        pro_run=pro,
        pro_predictions_jsonl=pro / "predictions.jsonl",
        pro_grade=pro_grade,
        interactive_report=interactive_report,
        interactive_drop=interactive_drop,
        interactive_spec=interactive_spec,
        eval_config=eval_config,
        interactive_config=interactive_config,
        provenance=provenance,
    )


def test_build_and_write_report_from_one_canonical_object(tmp_path):
    inputs = _fixtures(tmp_path)
    generated_at = "2026-07-30T20:00:00Z"
    report = reporter.build_report(inputs, generated_at)

    assert report["status"] == "complete"
    assert report["benchmarks"]["langbridge"]["totals"]["tasks"] == 18
    assert report["benchmarks"]["pro"]["totals"]["tasks"] == 30
    assert report["benchmarks"]["pro"]["prompt_protocol"]["score_eligible"] is True
    assert report["benchmarks"]["interactive"]["totals"]["tasks"] == 1
    assert report["remediation"]["deleted_quota_polluted"]["total"] == 26
    assert (
        report["remediation"]["deleted_quota_polluted"]["langbridge_task_ids"]
        == sorted(LB_RERUN_IDS)
    )
    assert (
        report["remediation"]["retained_valid"]["pro_task_ids"]
        == PRO_RETAINED_IDS
    )
    assert report["models"]["attribution_basis"]["langbridge"]["kind"] == (
        "launcher_and_config_reconstruction"
    )
    assert report["models"]["attribution_basis"]["interactive"]["kind"] == (
        "embedded_runtime_config"
    )
    assert "only Interactive embeds runtime config" in report["models"]["limitation"]
    assert report["benchmarks"]["pro"]["official_grader"] == {
        "repository": "https://example.test/SWE-bench_Pro-os.git",
        "commit": "fixture-commit",
        "script_path": str(inputs.pro_run / "swe_bench_pro_eval.py"),
        "script_sha256": _sha256(inputs.pro_run / "swe_bench_pro_eval.py"),
        "mode": "fixture Docker mode",
    }
    recheck = report["benchmarks"]["pro"]["resource_recheck"]
    assert len(recheck["invalid_task_ids"]) == 9
    assert recheck["provisional_raw"]["disposition"] == "discarded_as_invalid"
    assert recheck["resource_controls"] == {
        "rosetta_enabled": True,
        "docker_nano_cpus": 2_000_000_000,
        "goflags": "-p=1",
        "gomaxprocs": 2,
    }
    assert len(recheck["results"]) == 9
    assert len(recheck["canonical_log_paths"]) == 18
    assert recheck["nodebb_51_single_worker_caveat"]["canonical_result"] is False
    assert all(
        task["candidate_diff_sha256"]
        for task in report["benchmarks"]["langbridge"]["tasks"]
    )
    assert (
        report["task_quality_disposition"]["langbridge"]["current_human_review"]
        == {
            "reviewed_count": 29,
            "active_count": 18,
            "dropped_count": 11,
            "dropped_ids": [f"lb-dropped-{index:02d}" for index in range(11)],
            "dropped": [
                {
                    "task_id": f"lb-dropped-{index:02d}",
                    "reason": f"LangBridge drop reason {index:02d}",
                    "dropped_at": "2026-07-29T00:00:00Z",
                }
                for index in range(11)
            ],
        }
    )
    assert (
        report["task_quality_disposition"]["interactive"]["active_rewrite"][
            "baseline_comparable_after_rewrite"
        ]
        is False
    )
    assert report["task_quality_disposition"]["langbridge"]["prior_dropped"][0] == {
        "task_id": "lb-dropped-11",
        "reason": "LangBridge drop reason 11",
        "dropped_at": "2026-07-27T00:00:00Z",
    }
    assert report["task_quality_disposition"]["interactive"]["dropped"][0] == {
        "task_id": "interactive-drop-0",
        "reason": "Interactive drop reason 0",
    }
    assert (
        report["task_quality_disposition"]["langbridge"]["active_rewrite_count"]
        == 11
    )
    assert len(
        report["task_quality_disposition"]["langbridge"]["active_rewrites"]
    ) == 11
    assert all(
        "sha256" in source for source in report["artifacts"]["source_files"]
    )
    assert (
        report["benchmarks"]["pro"]["sources"]["predictions_jsonl"]
        == str(inputs.pro_predictions_jsonl)
    )

    output_json, output_md = tmp_path / "final.json", tmp_path / "final.md"
    written, json_sha, md_sha = reporter.write_reports(
        inputs, output_json, output_md, generated_at
    )
    assert json.loads(output_json.read_text()) == written == report
    markdown = output_md.read_text()
    assert "## 题目质量处置" in markdown
    assert "original LangBridge rewrite reason" in markdown
    assert "LangBridge rewrite reason old-task-00" in markdown
    assert "original Interactive rewrite reason" in markdown
    assert "本轮人肉检查 29 题" in markdown
    assert "### LangBridge 本轮新增 drop（11）" in markdown
    assert "LangBridge drop reason 02" in markdown
    assert "### LangBridge 历史 drop（2）" in markdown
    assert "LangBridge drop reason 12" in markdown
    assert "### Interactive drop（13）" in markdown
    assert "Interactive drop reason 0" in markdown
    assert "### LangBridge active rewrites（11）" in markdown
    assert "fixture-commit" in markdown
    assert "fixture Docker mode" in markdown
    assert "Provisional raw：5/30" in markdown
    assert "discarded_as_invalid" in markdown
    assert "Resource-safe recheck" in markdown
    assert "166 PASSED / 11 FAILED" in markdown
    assert "MaxRetries / ECONNREFUSED" in markdown
    assert "canonical official 结果保留 `false`" in markdown
    assert "launcher_and_config_reconstruction" in markdown
    assert "embedded_runtime_config" in markdown
    assert len(json_sha) == len(md_sha) == 64


def test_legacy_pro_prompt_protocol_is_excluded_from_scoring(tmp_path):
    inputs = _fixtures(tmp_path)
    run_path = inputs.pro_run / "run_summary.json"
    run = json.loads(run_path.read_text())
    run.pop("prompt_protocol")
    for summary in run["summaries"]:
        summary.pop("prompt_sha256")
    _write(run_path, run)

    report = reporter.build_report(inputs, "2026-07-30T20:00:00Z")
    pro = report["benchmarks"]["pro"]
    markdown = reporter.render_markdown(report)

    assert report["status"] == "complete_with_protocol_invalid_pro"
    assert pro["prompt_protocol"]["status"] == "protocol_invalid"
    assert pro["prompt_protocol"]["score_eligible"] is False
    assert pro["totals"]["resolved"] is None
    assert pro["totals"]["score_eligible_tasks"] == 0
    assert pro["totals"]["diagnostic_resolved"] == 6
    assert pro["pass_rate"] is None
    assert "INVALID (prompt protocol)" in markdown
    assert "**6/30 (20.00%)**" not in markdown
    assert "不能与官方成绩比较" in markdown


def test_tampered_pro_prompt_artifact_is_excluded_from_scoring(tmp_path):
    inputs = _fixtures(tmp_path)
    instance_id = PRO_RETAINED_IDS[0]
    prompt_path = inputs.pro_run / "artifacts" / instance_id / "agent_prompt.txt"
    prompt_path.write_text("different prompt", encoding="utf-8")

    report = reporter.build_report(inputs, "2026-07-30T20:00:00Z")
    protocol = report["benchmarks"]["pro"]["prompt_protocol"]

    assert protocol["score_eligible"] is False
    assert protocol["mismatched_prompt_artifact_ids"] == [instance_id]
    assert report["benchmarks"]["pro"]["pass_rate"] is None


def test_quota_detection_requires_wrapper_level_failure_signature():
    ordinary = json.dumps(
        {"report": "I investigated Error code: 402 and Insufficient Balance handling."}
    )
    actual = json.dumps(
        {
            "report": (
                "Request failed: Error code: 402 - "
                "{'error': {'message': 'Insufficient Balance'}}"
            )
        }
    )
    daily_quota = json.dumps(
        {"error": "API daily token quota is exhausted (provider TPD limit)."}
    )
    transport = json.dumps(
        {
            "report": (
                "Request failed: peer closed connection without sending complete "
                "message body (incomplete chunked read)"
            )
        }
    )
    other_failure = json.dumps({"report": "Request failed: upstream unavailable"})
    ordinary_daily = json.dumps(
        {"report": "I fixed API daily token quota is exhausted handling."}
    )
    assert reporter._quota_failure(ordinary) is False
    assert reporter._quota_failure(actual) is True
    assert reporter._quota_failure(daily_quota) is True
    assert reporter._quota_failure(ordinary_daily) is False
    assert reporter._quota_failure(transport) is False
    assert reporter._provider_failure_type(transport) == "transport"
    assert reporter._provider_failure_type(other_failure) == "other"


def test_official_grade_instance_set_must_match_predictions(tmp_path):
    inputs = _fixtures(tmp_path)
    official = json.loads(inputs.pro_grade.read_text())
    official.pop(next(iter(official)))
    _write(inputs.pro_grade, official)

    with pytest.raises(reporter.ReportError, match="pro_official_ids"):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")


def test_pro_prediction_formats_must_match(tmp_path):
    inputs = _fixtures(tmp_path)
    rows = [
        json.loads(line)
        for line in inputs.pro_predictions_jsonl.read_text().splitlines()
    ]
    rows[4]["model_patch"] = "diff --git a/a b/a\n+different\n"
    _write_jsonl(inputs.pro_predictions_jsonl, rows)

    with pytest.raises(reporter.ReportError, match="pro_prediction_formats_match"):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")


@pytest.mark.parametrize(
    ("target", "check"),
    [
        ("raw", "pro_raw_sample_sha256"),
        ("grader", "pro_official_grader_script_sha256"),
        ("eval_config", "provenance_eval_config_sha256"),
        ("interactive_config", "provenance_interactive_config_sha256"),
    ],
)
def test_declared_evidence_hashes_reject_tampering(tmp_path, target, check):
    inputs = _fixtures(tmp_path)
    provenance = json.loads(inputs.provenance.read_text())
    paths = {
        "raw": Path(provenance["pro"]["raw_sample_path"]),
        "grader": Path(provenance["pro"]["official_grader"]["script_path"]),
        "eval_config": inputs.eval_config,
        "interactive_config": inputs.interactive_config,
    }
    path = paths[target]
    if target == "raw":
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]["tampered"] = True
        _write_jsonl(path, rows)
    elif target.endswith("config"):
        value = json.loads(path.read_text())
        value["tampered"] = True
        _write(path, value)
    else:
        path.write_text(path.read_text() + "# tampered\n", encoding="utf-8")

    with pytest.raises(reporter.ReportError, match=check):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")


def test_raw_sample_order_must_match_run_summary(tmp_path):
    inputs = _fixtures(tmp_path)
    provenance = json.loads(inputs.provenance.read_text())
    raw_path = Path(provenance["pro"]["raw_sample_path"])
    rows = [json.loads(line) for line in raw_path.read_text().splitlines()]
    rows[0], rows[1] = rows[1], rows[0]
    _write_jsonl(raw_path, rows)
    provenance["pro"]["raw_sample_sha256"] = _sha256(raw_path)
    _write(inputs.provenance, provenance)

    with pytest.raises(reporter.ReportError, match="pro_raw_sample_ordered_unique"):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")


def test_cleanup_ids_must_match_report_phases(tmp_path):
    inputs = _fixtures(tmp_path)
    provenance = json.loads(inputs.provenance.read_text())
    provenance["cleanup"]["deleted_quota_polluted"]["pro_task_ids"][-1] = (
        "instance_not_in_run"
    )
    _write(inputs.provenance, provenance)

    with pytest.raises(reporter.ReportError, match="pro_cleanup_partition_matches_run"):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")


def test_langbridge_candidate_and_f2p_evidence_is_checked(tmp_path):
    inputs = _fixtures(tmp_path)
    summary_path = next(inputs.langbridge_retained.glob("*/summary.json"))
    candidate_path = summary_path.parent / "candidate.diff"
    candidate_path.write_text(candidate_path.read_text() + "x", encoding="utf-8")

    with pytest.raises(reporter.ReportError, match="candidate.diff length"):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")

    inputs = _fixtures(tmp_path / "f2p")
    grade_path = next(inputs.langbridge_retained.glob("*/grade.json"))
    grade = json.loads(grade_path.read_text())
    grade["f2p_total"] = 2
    _write(grade_path, grade)

    with pytest.raises(reporter.ReportError, match="F2P counts"):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")

    inputs = _fixtures(tmp_path / "p2p")
    grade_path = next(inputs.langbridge_retained.glob("*/grade.json"))
    grade = json.loads(grade_path.read_text())
    grade["outcomes"]["regression_case"] = "FAILED"
    _write(grade_path, grade)

    with pytest.raises(reporter.ReportError, match="regressions"):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")


def test_langbridge_allows_timeout_without_candidate_and_extra_outcomes(tmp_path):
    inputs = _fixtures(tmp_path)
    summary_path = next(inputs.langbridge_retained.glob("*/summary.json"))
    summary = json.loads(summary_path.read_text())
    summary.update(
        {
            "gt_pass": False,
            "grade_status": "not_graded",
            "diff_chars": 0,
            "timed_out": True,
            "error": "agent timed out after 1800s",
        }
    )
    _write(summary_path, summary)
    (summary_path.parent / "candidate.diff").unlink()
    report = reporter.build_report(inputs, "2026-07-30T20:00:00Z")
    task = next(
        row
        for row in report["benchmarks"]["langbridge"]["tasks"]
        if row["task_id"] == summary["task_id"]
    )
    assert task["candidate_diff_path"] is None
    assert task["candidate_diff_sha256"] is None

    inputs = _fixtures(tmp_path / "extra")
    grade_path = next(inputs.langbridge_retained.glob("*/grade.json"))
    grade = json.loads(grade_path.read_text())
    grade["outcomes"]["extra_outcome"] = "PASSED"
    _write(grade_path, grade)
    reporter.build_report(inputs, "2026-07-30T20:00:00Z")


def test_resource_recheck_evidence_and_canonical_results_are_checked(tmp_path):
    inputs = _fixtures(tmp_path)
    provenance = json.loads(inputs.provenance.read_text())
    resource = provenance["pro"]["resource_recheck"]
    script_path = Path(resource["evidence"]["script_path"])
    script_path.write_text(script_path.read_text() + "# tampered\n", encoding="utf-8")
    with pytest.raises(
        reporter.ReportError,
        match="pro_resource_recheck_script_sha256",
    ):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")

    inputs = _fixtures(tmp_path / "result")
    provenance = json.loads(inputs.provenance.read_text())
    resource = provenance["pro"]["resource_recheck"]
    result_path = Path(resource["evidence"]["eval_results_path"])
    results = json.loads(result_path.read_text())
    task_id = next(iter(results))
    results[task_id] = not results[task_id]
    _write(result_path, results)
    resource["evidence"]["eval_results_sha256"] = _sha256(result_path)
    _write(inputs.provenance, provenance)
    with pytest.raises(
        reporter.ReportError,
        match="pro_resource_recheck_matches_canonical",
    ):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")


def test_resource_recheck_canonical_logs_reject_toolchain_segfault(tmp_path):
    inputs = _fixtures(tmp_path)
    provenance = json.loads(inputs.provenance.read_text())
    task_id = provenance["pro"]["resource_recheck"]["invalid_task_ids"][0]
    stderr = (
        inputs.pro_grade.parent
        / task_id
        / "langbridge-l4_stderr.log"
    )
    stderr.write_text(
        "go tool compile: signal: segmentation fault\n",
        encoding="utf-8",
    )

    with pytest.raises(
        reporter.ReportError,
        match="pro_resource_recheck_no_toolchain_segfaults",
    ):
        reporter.build_report(inputs, "2026-07-30T20:00:00Z")
