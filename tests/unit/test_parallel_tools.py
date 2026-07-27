import json
import threading
import time

import pytest

from langbridge_code.agents.common import control
from langbridge_code.agents.common.parallel_tools import (
    CompletionDrivenToolRunner,
    _with_eval_tool_timing,
    partition_tool_calls,
    run_tool_calls,
)
from langbridge_code.tools.concurrency import is_concurrency_safe


@pytest.fixture(autouse=True)
def parallel_agents_enabled(monkeypatch):
    monkeypatch.setattr("langbridge_code.settings.PARALLEL_AGENTS_ENABLED", True)
    monkeypatch.setattr(
        "langbridge_code.agents.common.parallel_tools.PARALLEL_AGENTS_ENABLED",
        True,
    )


def test_is_concurrency_safe_read_only_tools():
    assert is_concurrency_safe("read_file", {"path": "a.py"})
    assert is_concurrency_safe("grep", {"pattern": "x"})
    assert is_concurrency_safe("glob", {"pattern": "*.py"})
    assert is_concurrency_safe("agent_explorer", {"prompt": "look"})
    assert is_concurrency_safe("agent_worker", {"prompt": "do"})
    assert not is_concurrency_safe("agent_planner", {"prompt": "plan"})
    assert not is_concurrency_safe("Edit", {"path": "a.py"})
    assert not is_concurrency_safe("write", {"path": "a.py"})
    assert not is_concurrency_safe("unknown_tool", {})


def test_is_concurrency_safe_bash_depends_on_command():
    assert is_concurrency_safe("bash", {"command": "git log --oneline"})
    assert is_concurrency_safe("bash", {"command": "ls -la src/"})
    assert is_concurrency_safe("bash", {"command": "pytest -q 2>&1 | tail -5"})
    assert is_concurrency_safe("bash", {"command": "grep -r foo . 2>/dev/null"})
    assert not is_concurrency_safe("bash", {"command": "rm -rf build/"})
    assert not is_concurrency_safe("bash", {"command": "git commit -m x"})
    assert not is_concurrency_safe("bash", {"command": "echo hi > out.txt"})
    assert not is_concurrency_safe("bash", {"command": "make test >> log.txt"})
    assert not is_concurrency_safe("bash", {"command": ""})
    assert not is_concurrency_safe("bash", {})


def test_partition_groups_consecutive_safe_calls():
    def call(name, call_id, arguments=None):
        item = {"name": name, "call_id": call_id}
        if arguments is not None:
            item["arguments"] = json.dumps(arguments)
        return item

    batches = partition_tool_calls(
        [
            call("read_file", "1"),
            call("grep", "2"),
            call("Edit", "3"),
            call("read_file", "4"),
            call("bash", "5", {"command": "git status"}),
            call("bash", "6", {"command": "rm -rf x"}),
        ]
    )
    shape = [(b.concurrency_safe, [c["call_id"] for c in b.calls]) for b in batches]
    assert shape == [
        (True, ["1", "2"]),
        (False, ["3"]),
        (True, ["4", "5"]),
        (False, ["6"]),
    ]


def test_partition_unparseable_arguments_fail_closed():
    batches = partition_tool_calls(
        [{"name": "read_file", "call_id": "1", "arguments": "{not json"}]
    )
    assert not batches[0].concurrency_safe


def test_pending_calls_reports_live_work_then_empties():
    release = threading.Event()

    def run_fn(call):
        release.wait(timeout=2)
        return {"call_id": call["call_id"], "output": "done"}

    call = {
        "name": "agent_worker",
        "call_id": "w1",
        "arguments": '{"task_name":"task-1"}',
    }
    with CompletionDrivenToolRunner(run_fn, max_workers=1) as runner:
        runner.submit([call])
        pending = runner.pending_calls()
        assert len(pending) == 1
        assert pending[0]["call"]["call_id"] == "w1"
        assert pending[0]["running_for_s"] >= 0

        release.set()
        completed = runner.drain_completed(wait_for_one=True)
        assert [item.call["call_id"] for item in completed] == ["w1"]
        assert runner.pending_calls() == []
        assert not runner.has_pending()


def test_run_tool_calls_preserves_order():
    started = []
    lock = threading.Lock()

    def run_fn(call):
        delay = 0.03 if call["call_id"] == "slow" else 0.01
        time.sleep(delay)
        with lock:
            started.append(call["call_id"])
        return {"call_id": call["call_id"], "output": call["call_id"]}

    calls = [
        {"name": "agent_explorer", "call_id": "slow"},
        {"name": "agent_explorer", "call_id": "fast"},
    ]
    outputs = run_tool_calls(run_fn, calls, max_workers=2)
    assert [item["output"] for item in outputs] == ["slow", "fast"]
    assert set(started) == {"slow", "fast"}


def test_parallel_calls_inherit_trace_context():
    from langbridge_code.util.trace_log import (
        TraceContext,
        get_trace_context,
        set_trace_context,
    )

    seen = {}
    lock = threading.Lock()

    def run_fn(call):
        ctx = get_trace_context()
        with lock:
            seen[call["call_id"]] = None if ctx is None else ctx.trace_id
        return {"call_id": call["call_id"], "output": "ok"}

    calls = [
        {"name": "agent_worker", "call_id": "a"},
        {"name": "agent_worker", "call_id": "b"},
    ]
    set_trace_context(TraceContext(run_log_path="run.log", trace_id="turn-42"))
    try:
        run_tool_calls(run_fn, calls, max_workers=2)
    finally:
        set_trace_context(None)

    assert seen == {"a": "turn-42", "b": "turn-42"}


def test_parallel_calls_inherit_workspace_root(tmp_path):
    """Pool threads must see the caller's worktree root, not the default one."""
    from langbridge_code.agents.common.workspace import (
        get_workspace_root,
        workspace_scope,
    )

    seen = {}
    lock = threading.Lock()

    def run_fn(call):
        with lock:
            seen[call["call_id"]] = get_workspace_root()
        return {"call_id": call["call_id"], "output": "ok"}

    calls = [
        {"name": "read_file", "call_id": "a", "arguments": "{}"},
        {"name": "read_file", "call_id": "b", "arguments": "{}"},
    ]
    with workspace_scope(tmp_path):
        run_tool_calls(run_fn, calls, max_workers=2)

    assert seen == {"a": tmp_path.resolve(), "b": tmp_path.resolve()}


def test_completion_runner_calls_inherit_trace_context():
    from langbridge_code.util.trace_log import (
        TraceContext,
        get_trace_context,
        set_trace_context,
    )

    seen = {}
    lock = threading.Lock()

    def run_fn(call):
        ctx = get_trace_context()
        with lock:
            seen[call["call_id"]] = None if ctx is None else ctx.trace_id
        return {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": "ok",
        }

    set_trace_context(TraceContext(run_log_path="run.log", trace_id="turn-7"))
    runner = CompletionDrivenToolRunner(run_fn, max_workers=2)
    try:
        runner.submit(
            [
                {"name": "agent_worker", "call_id": "a"},
                {"name": "agent_worker", "call_id": "b"},
            ]
        )
        while runner.has_pending():
            runner.drain_completed(wait_for_one=True)
    finally:
        runner.close()
        set_trace_context(None)

    assert seen == {"a": "turn-7", "b": "turn-7"}


def test_run_tool_calls_parallel_workers():
    order = []

    def run_fn(call):
        time.sleep(0.02)
        order.append(call["call_id"])
        return {"call_id": call["call_id"], "output": "ok"}

    calls = [
        {"name": "agent_worker", "call_id": "a"},
        {"name": "agent_worker", "call_id": "b"},
    ]
    outputs = run_tool_calls(run_fn, calls, max_workers=2)
    assert len(outputs) == 2
    assert set(order) == {"a", "b"}


def test_run_tool_calls_mixed_batch_keeps_output_order():
    def run_fn(call):
        # Parallel reads sleep so a serial scheduler bug would still pass;
        # order is asserted on outputs, not on execution timing.
        if call["name"] == "read_file":
            time.sleep(0.01)
        return {"call_id": call["call_id"], "output": call["call_id"]}

    calls = [
        {"name": "read_file", "call_id": "1"},
        {"name": "read_file", "call_id": "2"},
        {"name": "Edit", "call_id": "3"},
        {"name": "read_file", "call_id": "4"},
    ]
    outputs = run_tool_calls(run_fn, calls, max_workers=4)
    assert [item["output"] for item in outputs] == ["1", "2", "3", "4"]


def test_run_tool_calls_serial_when_mixed_with_planner():
    order = []

    def run_fn(call):
        order.append(call["call_id"])
        return {"call_id": call["call_id"], "output": "ok"}

    calls = [
        {"name": "agent_explorer", "call_id": "1"},
        {"name": "agent_planner", "call_id": "2"},
    ]
    run_tool_calls(run_fn, calls, max_workers=4)
    assert order == ["1", "2"]


def test_parallel_agents_disabled_runs_serial(monkeypatch):
    monkeypatch.setattr("langbridge_code.settings.PARALLEL_AGENTS_ENABLED", False)
    monkeypatch.setattr(
        "langbridge_code.agents.common.parallel_tools.PARALLEL_AGENTS_ENABLED",
        False,
    )
    order = []

    def run_fn(call):
        order.append(call["call_id"])
        return {"call_id": call["call_id"], "output": "ok"}

    calls = [
        {"name": "agent_worker", "call_id": "a"},
        {"name": "agent_worker", "call_id": "b"},
    ]
    run_tool_calls(run_fn, calls, max_workers=2)
    assert order == ["a", "b"]


def test_completion_runner_returns_first_result_without_waiting_for_batch():
    release_slow = threading.Event()

    def run_fn(call):
        if call["call_id"] == "slow":
            release_slow.wait(timeout=1)
        return {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": call["call_id"],
        }

    runner = CompletionDrivenToolRunner(run_fn, max_workers=2)
    try:
        runner.submit(
            [
                {"name": "agent_worker", "call_id": "slow"},
                {"name": "agent_worker", "call_id": "fast"},
            ]
        )
        completed = runner.drain_completed(wait_for_one=True)

        assert [item.output["output"] for item in completed] == ["fast"]
        assert runner.has_pending()

        release_slow.set()
        completed = runner.drain_completed(wait_for_one=True)
        assert [item.output["output"] for item in completed] == ["slow"]
        assert not runner.has_pending()
    finally:
        release_slow.set()
        runner.close()


def test_completion_runner_converts_background_exception_to_tool_error():
    def run_fn(_call):
        raise RuntimeError("boom")

    with CompletionDrivenToolRunner(run_fn, max_workers=1) as runner:
        runner.submit([{"name": "agent_worker", "call_id": "broken"}])
        completed = runner.drain_completed(wait_for_one=True)

    assert completed[0].output["call_id"] == "broken"
    assert completed[0].output["output"] == "Tool error: boom"


def test_completion_runner_wait_is_interruptible():
    release_worker = threading.Event()

    def run_fn(call):
        release_worker.wait(timeout=1)
        return {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": "done",
        }

    runner = CompletionDrivenToolRunner(run_fn, max_workers=1)
    control.clear_stop()
    try:
        runner.submit([{"name": "agent_worker", "call_id": "slow"}])
        threading.Timer(0.05, control.request_stop).start()
        with pytest.raises(control.StopRequested):
            runner.drain_completed(wait_for_one=True)
    finally:
        release_worker.set()
        control.clear_stop()
        runner.close()


def test_background_thread_records_eval_telemetry():
    """Worker-pool threads must see the parent telemetry collector."""
    from util import telemetry

    thread_ids = []

    def run_fn(call):
        thread_ids.append(threading.get_ident())
        # Inner tool-style record (what Worker does via _with_eval_tool_timing).
        telemetry.record_tool_call(
            tool="read_file",
            latency_s=0.001,
            arguments={"path": "foo.py"},
            agent="Worker",
        )
        return {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": "ok",
        }

    main_tid = threading.get_ident()
    with telemetry.start_telemetry() as tel:
        with CompletionDrivenToolRunner(run_fn, max_workers=1) as runner:
            runner.submit([{"name": "agent_worker", "call_id": "bg"}])
            completed = runner.drain_completed(wait_for_one=True)
        snap = tel.snapshot()

    assert completed[0].output["output"] == "ok"
    assert thread_ids and thread_ids[0] != main_tid
    # Outer wrap from submit + inner record_tool_call above.
    tools = [e["tool"] for e in snap["detail_events"] if e.get("type") == "tool_call"]
    assert "agent_worker" in tools
    assert "read_file" in tools


def test_eval_tool_timing_records_all_arguments():
    from util import telemetry

    def run_fn(call):
        return {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": "ok",
        }

    timed = _with_eval_tool_timing(run_fn)
    with telemetry.start_telemetry() as tel:
        timed(
            {
                "name": "read_webpage",
                "call_id": "w1",
                "arguments": (
                    '{"description":"docs","url":"https://example.com/x",'
                    '"max_chars":1000}'
                ),
            }
        )
        timed(
            {
                "name": "write",
                "call_id": "w2",
                "arguments": json.dumps(
                    {"description": "save", "path": "a.py", "contents": "x" * 600}
                ),
            }
        )
        snap = tel.snapshot()

    web = next(e for e in snap["detail_events"] if e.get("tool") == "read_webpage")
    assert web["calls"][0]["arguments"] == {
        "description": "docs",
        "url": "https://example.com/x",
        "max_chars": 1000,
    }
    write = next(e for e in snap["detail_events"] if e.get("tool") == "write")
    contents = write["calls"][0]["arguments"]["contents"]
    assert contents.endswith("…")
    assert len(contents) == 501
