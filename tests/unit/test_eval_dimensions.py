"""Unit tests for static-analysis scoring and telemetry aggregates."""
import json
from pathlib import Path

from langbridge_eval import dimensions, telemetry


def test_static_analysis_defaults_globally():
    assert dimensions.resolve_static_commands({}) == ["ruff", "mypy", "bandit"]
    assert dimensions.resolve_static_commands({"static_analysis": False}) is None
    assert dimensions.resolve_static_commands({"static_analysis": {"commands": []}}) is None
    assert dimensions.resolve_static_commands(
        {"static_analysis": {"commands": ["ruff"]}}
    ) == ["ruff"]


def test_static_exe_prefers_refvenv(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    bin_dir = repo / ".refvenv" / "bin"
    bin_dir.mkdir(parents=True)
    ruff = bin_dir / "ruff"
    ruff.write_text("#!/bin/true\n", encoding="utf-8")
    ruff.chmod(0o755)
    monkeypatch.setattr(dimensions.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert dimensions._resolve_static_exe("ruff", str(repo)) == str(ruff)
    assert dimensions._resolve_static_exe("mypy", str(repo)) == "/usr/bin/mypy"


def test_changed_py_files_extracts_touched_python_paths():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "diff --git a/old.py b/old.py\n"
        "--- a/old.py\n"
        "+++ /dev/null\n"
        "diff --git a/pkg/new.py b/pkg/new.py\n"
        "--- /dev/null\n"
        "+++ b/pkg/new.py\n"
    )
    assert dimensions.changed_py_files(diff) == ["pkg/new.py", "src/a.py"]
    assert dimensions.changed_py_files("") == []


def test_score_static_analysis_only_penalizes_new_findings(monkeypatch):
    counts = {"ruff": (3, "out"), "mypy": (0, ""), "bandit": (None, "not_installed")}
    monkeypatch.setattr(
        dimensions, "_count_findings", lambda name, repo, files: counts[name]
    )
    baseline = {"ruff": 3, "mypy": 1, "bandit": None}
    result = dimensions.score_static_analysis("/repo", {}, ["a.py"], baseline)
    by_cmd = {r["command"]: r for r in result["results"]}
    # ruff: 3 pre-existing findings, no new ones -> ok
    assert by_cmd["ruff"]["ok"] is True
    # mypy: went from 1 finding to 0 -> ok
    assert by_cmd["mypy"]["ok"] is True
    # bandit unusable both sides -> excluded from scoring
    assert by_cmd["bandit"]["ok"] is None
    assert result["score"] == 1.0


def test_score_static_analysis_flags_introduced_findings(monkeypatch):
    monkeypatch.setattr(
        dimensions, "_count_findings", lambda name, repo, files: (2, "boom")
    )
    result = dimensions.score_static_analysis(
        "/repo", {"static_analysis": {"commands": ["ruff", "mypy"]}}, ["a.py"],
        {"ruff": 0, "mypy": 2},
    )
    by_cmd = {r["command"]: r for r in result["results"]}
    assert by_cmd["ruff"]["ok"] is False
    assert by_cmd["ruff"]["new"] == 2
    assert by_cmd["mypy"]["ok"] is True
    assert result["score"] == 0.5


def test_score_static_analysis_skips_without_changed_files():
    result = dimensions.score_static_analysis("/repo", {}, [], {})
    assert result["skipped"] is True
    assert result["score"] is None


def test_telemetry_snapshot_aggregates():
    with telemetry.start_telemetry() as tel:
        telemetry.record_model_call(
            agent="Worker",
            input_tokens=100,
            output_tokens=10,
            time_to_first_token_s=0.2,
            total_latency_s=1.0,
        )
        telemetry.record_model_call(
            agent="Worker",
            input_tokens=50,
            output_tokens=5,
            time_to_first_token_s=0.1,
            total_latency_s=0.5,
        )
        telemetry.record_model_call(
            agent="Reviewer",
            input_tokens=50,
            output_tokens=5,
            time_to_first_token_s=0.3,
            total_latency_s=2.0,
        )
        telemetry.record_tool_call(tool="bash", latency_s=0.04, arguments={"command": "pytest"})
        telemetry.record_tool_call(tool="bash", latency_s=0.01, arguments={"command": "ls"})
        telemetry.record_tool_call(tool="Edit", latency_s=0.001, arguments={"path": "a.py"})
        telemetry.record_agent_task(role="coder", latency_s=1.5, task="fix")
        telemetry.record_agent_task(role="reviewer", latency_s=0.8, task="review")
        snap = tel.snapshot()

    by_type = {}
    for e in snap["events"]:
        key = (e["type"], e.get("agent") or e.get("tool"))
        by_type[key] = e

    worker = by_type[("model_call", "Worker")]
    assert worker["count"] == 2
    assert worker["avg_input_tokens"] == 75.0
    assert worker["avg_output_tokens"] == 7.5
    assert worker["total_latency_s"] == 1.5
    assert worker["avg_latency_s"] == 0.75
    assert worker["avg_time_to_first_token_s"] == 0.15
    assert "calls" not in worker
    assert "elapsed_s" not in worker

    bash = by_type[("tool_call", "bash")]
    assert bash["count"] == 2
    assert bash["total_latency_s"] == 0.05
    assert bash["avg_latency_s"] == 0.025
    assert "calls" not in bash

    assert by_type[("tool_call", "Edit")]["count"] == 1
    assert len(snap["events"]) == 4

    detail_by = {}
    for e in snap["detail_events"]:
        detail_by[(e["type"], e.get("agent") or e.get("tool"))] = e
    detail_bash = detail_by[("tool_call", "bash")]
    assert len(detail_bash["calls"]) == 2
    assert detail_bash["calls"][0]["arguments"]["command"] == "pytest"
    assert "elapsed_s" in detail_bash["calls"][0]
    detail_worker = detail_by[("model_call", "Worker")]
    assert len(detail_worker["calls"]) == 2
    assert detail_worker["calls"][0]["input_tokens"] == 100
    assert "elapsed_s" in detail_worker["calls"][0]

    agg = snap["aggregates"]
    assert agg["total_model_latency_s"] == 3.5
    assert agg["avg_model_call_latency_s"] == 1.167  # (1.0 + 0.5 + 2.0) / 3
    assert agg["avg_time_to_first_token_s"] == 0.2
    assert agg["total_tool_latency_s"] == 0.051
    assert agg["coder_task_latencies_s"] == [1.5]
    assert agg["reviewer_task_latencies_s"] == [0.8]


def test_record_result_splits_report_and_detail(tmp_path, monkeypatch):
    from langbridge_eval import metrics

    monkeypatch.setenv("LANGBRIDGE_EVAL_RESULTS_DIR", str(tmp_path))
    rows = [
        {
            "task_id": "t1",
            "gt_pass": True,
            "telemetry": {
                "events": [
                    {
                        "type": "tool_call",
                        "tool": "bash",
                        "count": 1,
                        "total_latency_s": 0.01,
                        "avg_latency_s": 0.01,
                    }
                ],
                "detail_events": [
                    {
                        "type": "tool_call",
                        "tool": "bash",
                        "count": 1,
                        "total_latency_s": 0.01,
                        "avg_latency_s": 0.01,
                        "calls": [
                            {
                                "elapsed_s": 0.005,
                                "latency_s": 0.01,
                                "agent": "",
                                "arguments": {"command": "ls"},
                            }
                        ],
                    }
                ],
                "aggregates": {"total_tool_latency_s": 0.01},
            },
        }
    ]
    path = metrics.record_result("e2e", rows, model="docker", run_id="20260724-155347-report")
    assert path.endswith("20260724-155347-report.json")
    report = json.loads(Path(path).read_text())
    assert "calls" not in report["per_item"][0]["telemetry"]["events"][0]
    assert "detail_events" not in report["per_item"][0]["telemetry"]
    detail = json.loads(Path(str(tmp_path / "20260724-155347-report-detail.json")).read_text())
    assert detail["run_id"] == "20260724-155347-report"
    assert detail["per_item"][0]["events"][0]["calls"][0]["arguments"]["command"] == "ls"
