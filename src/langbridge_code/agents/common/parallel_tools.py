"""Parallel tool-call execution for the main agent loop (not an LLM tool)."""

import json
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

from langbridge_code.agents.common import control
from langbridge_code.settings import MAX_PARALLEL_TOOL_CALLS, PARALLEL_AGENTS_ENABLED
from langbridge_code.util.trace_log import get_trace_context, set_trace_context


def _bind_trace_context(run_fn, ctx):
    """Run ``run_fn`` in a worker thread under the parent's session + eval contexts.

    Trace context, eval telemetry, and the workspace root all use thread-local /
    ContextVar storage, so ThreadPool workers must re-bind them — otherwise
    session.md entries go missing and worktree workers' file tools would resolve
    paths against the main workspace instead of their worktree.
    """
    from langbridge_code.agents.common.workspace import (
        get_plan_file_override,
        get_workspace_root,
        plan_file_scope,
        workspace_scope,
    )
    from langbridge_eval import telemetry

    tel = telemetry.get_telemetry()
    workspace_root = get_workspace_root()
    plan_file = get_plan_file_override()

    def runner(call):
        set_trace_context(ctx)
        with (
            telemetry.telemetry_scope(tel),
            workspace_scope(workspace_root),
            plan_file_scope(plan_file),
        ):
            return run_fn(call)

    return runner


# Cap long string args (e.g. write/Edit bodies) so detail reports stay readable.
_TELEMETRY_ARG_CHARS = 500


def _telemetry_arguments(value):
    """Copy tool args for telemetry; truncate long strings."""
    if isinstance(value, str):
        if len(value) <= _TELEMETRY_ARG_CHARS:
            return value
        return value[:_TELEMETRY_ARG_CHARS] + "…"
    if isinstance(value, dict):
        return {str(k): _telemetry_arguments(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_telemetry_arguments(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _telemetry_arguments(str(value))


def _with_eval_tool_timing(run_fn):
    """Record tool latency into eval telemetry when a collector is active."""

    def runner(call):
        try:
            from langbridge_eval import telemetry
        except ImportError:
            return run_fn(call)

        if telemetry.get_telemetry() is None:
            return run_fn(call)
        name = call.get("name") or ""
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        recorded = _telemetry_arguments(arguments) if isinstance(arguments, dict) else {}
        start = time.perf_counter()
        try:
            return run_fn(call)
        finally:
            telemetry.record_tool_call(
                tool=name,
                latency_s=time.perf_counter() - start,
                arguments=recorded,
            )

    return runner

# Long-running calls whose results should wake the main agent independently.
BACKGROUND_TOOL_NAMES = frozenset({"agent_explorer", "agent_worker"})


@dataclass(frozen=True)
class CompletedToolCall:
    call: dict
    output: dict


class CompletionDrivenToolRunner:
    """Keep subagent calls running while the main agent handles early results."""

    def __init__(self, run_fn, *, max_workers: int | None = None):
        limit = max_workers if max_workers is not None else MAX_PARALLEL_TOOL_CALLS
        self._run_fn = run_fn
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(limit)))
        self._pending: dict[Future, dict] = {}

    def submit(self, calls) -> None:
        ctx = get_trace_context()
        run_fn = _bind_trace_context(_with_eval_tool_timing(self._run_fn), ctx)
        for call in calls:
            future = self._executor.submit(run_fn, call)
            self._pending[future] = call

    def has_pending(self) -> bool:
        return bool(self._pending)

    def drain_completed(self, *, wait_for_one: bool = False) -> list[CompletedToolCall]:
        if not self._pending:
            return []
        futures = set(self._pending)
        if wait_for_one:
            while True:
                control.checkpoint()
                done, _ = wait(futures, timeout=0.05, return_when=FIRST_COMPLETED)
                if done:
                    break
        done = [future for future in self._pending if future.done()]
        completed = []
        for future in done:
            call = self._pending.pop(future)
            try:
                output = future.result()
            except Exception as error:
                output = {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": f"Tool error: {error}",
                }
            completed.append(CompletedToolCall(call=call, output=output))
        return completed

    def close(self) -> None:
        # Running workers observe the shared stop signal. Waiting here prevents
        # them from mutating worktrees after their owning session has ended.
        self._executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()


@dataclass
class ToolCallBatch:
    """Either one non-concurrency-safe call, or consecutive safe calls."""

    concurrency_safe: bool
    calls: list


def partition_tool_calls(tool_calls) -> list[ToolCallBatch]:
    """Split a call list into batches, Claude Code style.

    Consecutive concurrency-safe calls collapse into one parallel batch;
    every unsafe call becomes its own serial batch. Original order is kept.
    """
    from langbridge_code.tools.concurrency import call_is_concurrency_safe

    batches: list[ToolCallBatch] = []
    for call in tool_calls:
        safe = call_is_concurrency_safe(call)
        if safe and batches and batches[-1].concurrency_safe:
            batches[-1].calls.append(call)
        else:
            batches.append(ToolCallBatch(concurrency_safe=safe, calls=[call]))
    return batches


def run_tool_calls(run_fn, tool_calls, *, max_workers: int | None = None):
    """Run concurrency-safe batches in a thread pool, the rest serially, in order."""
    timed = _with_eval_tool_timing(run_fn)
    limit = max_workers if max_workers is not None else MAX_PARALLEL_TOOL_CALLS
    outputs = []
    for batch in partition_tool_calls(tool_calls):
        parallel = (
            PARALLEL_AGENTS_ENABLED and batch.concurrency_safe and len(batch.calls) > 1
        )
        if not parallel:
            outputs.extend(timed(call) for call in batch.calls)
            continue
        workers = max(1, min(len(batch.calls), limit))
        bound_run_fn = _bind_trace_context(timed, get_trace_context())
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(bound_run_fn, call) for call in batch.calls]
            outputs.extend(future.result() for future in futures)
    return outputs
