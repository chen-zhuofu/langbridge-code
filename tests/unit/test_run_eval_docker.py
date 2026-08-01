import importlib.util
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

# run_eval_docker.py needs the optional "eval" dependency group (uv sync --group eval).
pytest.importorskip("datasets")
pytest.importorskip("swebench")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "eval" / "run_public_eval.py"
SPEC = importlib.util.spec_from_file_location("run_eval_docker", MODULE_PATH)
docker_eval = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(docker_eval)


def _pro_instance(instance_id: str, **overrides):
    row = {
        "instance_id": instance_id,
        "problem_statement": f"problem for {instance_id}",
        "requirements": f"requirements for {instance_id}",
        "interface": "No new interfaces are introduced.",
    }
    row.update(overrides)
    return row


def test_public_harness_default_timeout_is_two_hours():
    assert docker_eval.PUBLIC_EVAL_TIMEOUT_SECONDS == 7200


def test_format_problem_statement_matches_scale_template_exactly():
    instance = {
        "problem_statement": '"quoted\\nproblem"',
        "requirements": "- first\n- second",
        "interface": None,
    }

    assert docker_eval.format_problem_statement(instance, difficulty="pro") == (
        '"quoted\\nproblem"\n\n'
        "Requirements:\n"
        "- first\n- second\n\n"
        "New interfaces introduced:\n"
        "None"
    )


def test_format_problem_statement_leaves_verified_input_unchanged():
    assert (
        docker_eval.format_problem_statement(
            {"problem_statement": "  exact verified text\n"},
            difficulty="verified",
        )
        == "  exact verified text\n"
    )
    with pytest.raises(ValueError, match="unsupported public eval difficulty"):
        docker_eval.format_problem_statement({}, difficulty="lite")


def test_pro_go_resource_controls_match_resource_safe_grader_profile():
    instance = _pro_instance("go-task", repo_language="Go")

    assert docker_eval.docker_run_resource_args(instance, difficulty="pro") == [
        "--platform",
        "linux/amd64",
        "--cpus",
        "2",
    ]
    env = {}
    docker_eval.apply_instance_resource_env(env, instance, difficulty="pro")
    assert env == {"GOFLAGS": "-p=1", "GOMAXPROCS": "2"}

    assert docker_eval.docker_run_resource_args(
        instance, difficulty="verified"
    ) == []


def test_prepare_instance_workspace_resets_cleans_and_verifies_status():
    calls = []

    def fake_exec(container, command):
        calls.append((container, command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with patch.object(docker_eval, "container_exec", side_effect=fake_exec):
        docker_eval.prepare_instance_workspace("worker", "/app", "abc123")

    assert len(calls) == 2
    assert "git reset --hard abc123" in calls[0][1]
    assert "git clean -fdq" in calls[0][1]
    assert "git submodule update --recursive --force --no-fetch" in calls[0][1]
    assert "git submodule update --init" not in calls[0][1]
    assert "git submodule foreach --recursive" in calls[0][1]
    assert "git status --porcelain --untracked-files=all" in calls[1][1]


def test_prepare_instance_workspace_rejects_dirty_checkout():
    clean = subprocess.CompletedProcess("reset", 0, stdout="", stderr="")
    dirty = subprocess.CompletedProcess(
        "status",
        0,
        stdout="?? appendonlydir/appendonly.aof\n",
        stderr="",
    )
    with patch.object(docker_eval, "container_exec", side_effect=[clean, dirty]):
        with pytest.raises(RuntimeError, match="still dirty"):
            docker_eval.prepare_instance_workspace("worker", "/app", "abc123")


def test_bootstrap_container_agent_tools_prefers_native_ripgrep_installers():
    result = subprocess.CompletedProcess("tools", 0, stdout="", stderr="")
    with patch.object(docker_eval, "container_exec", return_value=result) as execute:
        returned = docker_eval.bootstrap_container_agent_tools("worker")

    assert returned is result
    command = execute.call_args.args[1]
    assert "command -v rg" in command
    assert "apk add --no-cache ripgrep" in command
    assert "apt-get install -y -qq ripgrep" in command
    assert execute.call_args.kwargs["timeout"] == 300


def test_capture_candidate_patch_diffs_from_base_and_excludes_runtime_noise():
    candidate = (
        "diff --git a/src/app.py b/src/app.py\n"
        "+fixed\n"
        "diff --git a/.langbridge/runtime.json b/.langbridge/runtime.json\n"
        "+noise\n"
    )
    result = subprocess.CompletedProcess("diff", 0, stdout=candidate, stderr="")
    with patch.object(docker_eval, "container_exec", return_value=result) as execute:
        patch_text = docker_eval.capture_candidate_patch(
            "worker",
            "/app",
            "abc123",
            docker_eval.REPO_RUNTIME_DIFF_EXCLUDES["NodeBB/NodeBB"],
        )

    command = execute.call_args.args[1]
    assert "git diff abc123 --cached" in command
    assert "git add -A -- ." in command
    assert "git reset -q abc123 --" in command
    assert "appendonlydir" in command
    assert "todo_list.md" in command
    assert ".langbridge" in command
    assert ":!" not in command
    assert "src/app.py" in patch_text
    assert ".langbridge/runtime.json" not in patch_text


def test_capture_candidate_patch_rejects_failed_git_diff():
    result = subprocess.CompletedProcess("diff", 1, stdout="", stderr="bad revision")
    with patch.object(docker_eval, "container_exec", return_value=result):
        with pytest.raises(RuntimeError, match="candidate diff capture failed"):
            docker_eval.capture_candidate_patch("worker", "/app", "missing")


def test_strip_binary_diff_hunks_keeps_text_changes():
    patch_text = (
        "diff --git a/core b/core\n"
        "new file mode 100644\n"
        "Binary files /dev/null and b/core differ\n"
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-before = True\n"
        "+after = True\n"
    )

    stripped = docker_eval.strip_binary_diff_hunks(patch_text)

    assert "Binary files" not in stripped
    assert "a/core" not in stripped
    assert "app.py" in stripped
    assert "+after = True" in stripped


def test_capture_candidate_patch_handles_ignored_langbridge_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-q")
    git("config", "user.name", "Eval Test")
    git("config", "user.email", "eval@example.invalid")
    (repo / ".gitignore").write_text(".langbridge/\n", encoding="utf-8")
    (repo / "app.py").write_text("before = True\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").stdout.strip()

    (repo / "app.py").write_text("after = True\n", encoding="utf-8")
    runtime = repo / ".langbridge"
    runtime.mkdir()
    (runtime / "runtime.json").write_text("{}\n", encoding="utf-8")

    def local_exec(_container, command):
        return subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
        )

    with patch.object(docker_eval, "container_exec", side_effect=local_exec):
        patch_text = docker_eval.capture_candidate_patch("local", str(repo), base)

    assert "app.py" in patch_text
    assert ".langbridge" not in patch_text


def test_parse_agent_stdout_uses_final_json_report():
    output = 'worker log\n{"report": "ok"}\n'
    assert docker_eval.parse_agent_stdout(output) == {"report": "ok"}
    assert docker_eval.parse_agent_stdout("worker log only") == {}


def test_build_agent_env_includes_eval_util_on_pythonpath():
    with patch.dict(os.environ, {"LANGBRIDGE_API_PROVIDER": "deepseek"}):
        env = docker_eval.build_agent_env("test-key", "")
    assert env["PYTHONPATH"] == docker_eval.CONTAINER_PYTHONPATH
    assert docker_eval.CONTAINER_EVAL in env["PYTHONPATH"].split(":")


def test_export_container_artifacts_skips_missing_paths(tmp_path):
    with patch.object(docker_eval, "container_exec", return_value=type("R", (), {"returncode": 1})()):
        exports = docker_eval.export_container_artifacts("container", tmp_path)
    assert exports == {}


def test_export_container_artifacts_copies_existing_paths(tmp_path):
    def fake_exec(container, command):
        path = command.removeprefix("test -e ")
        exists = path in {
            docker_eval.CONTAINER_LANGBRIDGE_ARTIFACTS,
            docker_eval.CONTAINER_ARTIFACTS,
        }
        return type("R", (), {"returncode": 0 if exists else 1})()

    def fake_docker(args, **kwargs):
        container_path = args[1].split(":", 1)[1]
        host_dir = Path(args[2])
        dest = host_dir / Path(container_path).name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "marker.txt").write_text(container_path, encoding="utf-8")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch.object(docker_eval, "container_exec", side_effect=fake_exec):
        with patch.object(docker_eval, "docker", side_effect=fake_docker):
            exports = docker_eval.export_container_artifacts("container", tmp_path)

    assert set(exports) == {"langbridge_artifacts", "lb_artifacts"}
    assert (tmp_path / exports["langbridge_artifacts"] / "marker.txt").read_text() == (
        docker_eval.CONTAINER_LANGBRIDGE_ARTIFACTS
    )


def test_timeout_salvage_stops_all_writers_before_diff_capture():
    events = []

    def fake_docker(args, **_kwargs):
        events.append(args[0])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def fake_capture(*_args, **_kwargs):
        events.append("capture")
        return "diff --git a/a b/a\n+partial\n"

    with patch.object(docker_eval, "docker", side_effect=fake_docker):
        with patch.object(
            docker_eval, "capture_candidate_patch", side_effect=fake_capture
        ):
            candidate = docker_eval.salvage_timeout_patch(
                "worker", "/app", "abc123"
            )

    assert events == ["kill", "wait", "start", "capture"]
    assert "+partial" in candidate


def test_timeout_salvage_reports_failed_barrier():
    def fake_docker(args, **_kwargs):
        return subprocess.CompletedProcess(
            args,
            1 if args[0] == "wait" else 0,
            stdout="",
            stderr="wait failed",
        )

    with patch.object(docker_eval, "docker", side_effect=fake_docker):
        with pytest.raises(RuntimeError, match=r"docker wait .* failed"):
            docker_eval.salvage_timeout_patch("worker", "/app", "abc123")


def test_empty_patch_is_retryable_infrastructure_failure():
    with pytest.raises(RuntimeError, match="produced no candidate patch"):
        docker_eval.require_candidate_patch(" \n")
    docker_eval.require_candidate_patch("diff --git a/a b/a\n+fix\n")


def test_write_run_checkpoint_persists_partial_pro_results_in_input_order(tmp_path):
    results = [
        (
            2,
            {
                "instance_id": "second",
                "model_name_or_path": docker_eval.MODEL_NAME,
                "model_patch": "patch-two",
            },
            {"instance_id": "second", "has_patch": True},
        ),
        (
            1,
            {
                "instance_id": "first",
                "model_name_or_path": docker_eval.MODEL_NAME,
                "model_patch": "patch-one",
            },
            {"instance_id": "first", "has_patch": True},
        ),
    ]

    docker_eval.write_run_checkpoint(
        tmp_path,
        difficulty="pro",
        dataset="ScaleAI/SWE-bench_Pro",
        split="test",
        workers=3,
        indexed_results=results,
    )

    lines = [
        json.loads(line)
        for line in (tmp_path / "predictions.jsonl").read_text().splitlines()
    ]
    pro = json.loads((tmp_path / "predictions-pro.json").read_text())
    summary = json.loads((tmp_path / "run_summary.json").read_text())

    assert [row["instance_id"] for row in lines] == ["first", "second"]
    assert pro == [
        {"instance_id": "first", "patch": "patch-one", "prefix": docker_eval.MODEL_NAME},
        {"instance_id": "second", "patch": "patch-two", "prefix": docker_eval.MODEL_NAME},
    ]
    assert summary["completed"] == 2
    assert summary["prompt_protocol"] == docker_eval.prompt_protocol("pro")


def test_load_resumable_results_keeps_clean_completion_and_retries_infra_error(
    tmp_path,
):
    instances = [
        _pro_instance("clean"),
        _pro_instance("infra-error"),
        _pro_instance("valid-timeout"),
        _pro_instance("empty-completion"),
        _pro_instance("missing"),
    ]
    results = [
        (
            1,
            {
                "instance_id": "clean",
                "model_name_or_path": docker_eval.MODEL_NAME,
                "model_patch": (
                    "diff --git a/core b/core\n"
                    "Binary files /dev/null and b/core differ\n"
                    "diff --git a/app.py b/app.py\n"
                    "+fixed\n"
                ),
            },
            {
                "instance_id": "clean",
                "returncode": 0,
                "timed_out": False,
                "error": "",
                "prompt_sha256": docker_eval.prompt_sha256(
                    instances[0], difficulty="pro"
                ),
            },
        ),
        (
            2,
            {
                "instance_id": "infra-error",
                "model_name_or_path": docker_eval.MODEL_NAME,
                "model_patch": "",
            },
            {
                "instance_id": "infra-error",
                "returncode": None,
                "timed_out": False,
                "error": "checkout is dirty",
                "prompt_sha256": docker_eval.prompt_sha256(
                    instances[1], difficulty="pro"
                ),
            },
        ),
        (
            3,
            {
                "instance_id": "valid-timeout",
                "model_name_or_path": docker_eval.MODEL_NAME,
                "model_patch": "diff --git a/partial.py b/partial.py\n+work\n",
            },
            {
                "instance_id": "valid-timeout",
                "returncode": None,
                "timed_out": True,
                "error": "agent timed out after 1800s",
                "timeout_patch_salvage": "succeeded",
                "prompt_sha256": docker_eval.prompt_sha256(
                    instances[2], difficulty="pro"
                ),
            },
        ),
        (
            4,
            {
                "instance_id": "empty-completion",
                "model_name_or_path": docker_eval.MODEL_NAME,
                "model_patch": "",
            },
            {
                "instance_id": "empty-completion",
                "returncode": 0,
                "timed_out": False,
                "error": "",
                "prompt_sha256": docker_eval.prompt_sha256(
                    instances[3], difficulty="pro"
                ),
            },
        ),
    ]
    docker_eval.write_run_checkpoint(
        tmp_path,
        difficulty="pro",
        dataset="ScaleAI/SWE-bench_Pro",
        split="test",
        workers=2,
        indexed_results=results,
    )

    resumed = docker_eval.load_resumable_results(
        tmp_path,
        instances=instances,
        difficulty="pro",
        dataset="ScaleAI/SWE-bench_Pro",
        split="test",
    )

    assert [prediction["instance_id"] for _, prediction, _ in resumed] == [
        "clean",
        "valid-timeout",
    ]
    assert "Binary files" not in resumed[0][1]["model_patch"]
    assert "app.py" in resumed[0][1]["model_patch"]
    assert resumed[0][2]["has_patch"] is True
    assert "partial.py" in resumed[1][1]["model_patch"]
    assert resumed[1][2]["has_patch"] is True


def test_resume_rejects_legacy_prompt_protocol(tmp_path):
    instance = _pro_instance("legacy")
    results = [
        (
            1,
            {
                "instance_id": "legacy",
                "model_name_or_path": docker_eval.MODEL_NAME,
                "model_patch": "diff --git a/a b/a\n+fix\n",
            },
            {
                "instance_id": "legacy",
                "returncode": 0,
                "timed_out": False,
                "error": "",
                "prompt_sha256": docker_eval.prompt_sha256(
                    instance, difficulty="pro"
                ),
            },
        )
    ]
    docker_eval.write_run_checkpoint(
        tmp_path,
        difficulty="pro",
        dataset="ScaleAI/SWE-bench_Pro",
        split="test",
        workers=2,
        indexed_results=results,
    )
    summary_path = tmp_path / "run_summary.json"
    summary = json.loads(summary_path.read_text())
    summary.pop("prompt_protocol")
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    assert (
        docker_eval.load_resumable_results(
            tmp_path,
            instances=[instance],
            difficulty="pro",
            dataset="ScaleAI/SWE-bench_Pro",
            split="test",
        )
        == []
    )
