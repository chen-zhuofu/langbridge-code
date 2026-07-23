"""Parallel tool-call execution for the main agent loop (not an LLM tool)."""

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

from langbridge_code.agents.common import control
from langbridge_code.settings import MAX_PARALLEL_TOOL_CALLS, PARALLEL_AGENTS_ENABLED
from langbridge_code.util.trace_log import get_trace_context, set_trace_context


def _bind_trace_context(run_fn, ctx):
    """Run ``run_fn`` in a worker thread under the parent's session trace context.

    The trace context is thread-local, so subagents dispatched onto pool threads
    would otherwise lose it and silently skip session.md. Re-binding it here makes
    every parallel subagent write into the same session trace as the main agent.
    """

    def runner(call):
        set_trace_context(ctx)
        return run_fn(call)

    return runner

# Subagent tools safe to run concurrently (read-only explorers, or isolated worktree workers).
PARALLEL_TOOL_NAMES = frozenset(
    {
        "agent_explorer",
        "agent_worker",
        "glob",
        "read_file",
        "grep",
        "read_webpage",
        "read_skill",
    }
)

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
        run_fn = _bind_trace_context(self._run_fn, ctx)
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


def can_run_tool_calls_in_parallel(tool_calls) -> bool:
    if not PARALLEL_AGENTS_ENABLED:
        return False
    if len(tool_calls) < 2:
        return False
    return all(call.get("name") in PARALLEL_TOOL_NAMES for call in tool_calls)


def run_tool_calls(run_fn, tool_calls, *, max_workers: int | None = None):
    """Run tool calls in parallel when every call is in PARALLEL_TOOL_NAMES."""
    if not can_run_tool_calls_in_parallel(tool_calls):
        return [run_fn(call) for call in tool_calls]

    limit = max_workers if max_workers is not None else MAX_PARALLEL_TOOL_CALLS
    workers = max(1, min(len(tool_calls), limit))
    outputs = [None] * len(tool_calls)
    bound_run_fn = _bind_trace_context(run_fn, get_trace_context())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(bound_run_fn, call) for call in tool_calls]
        for index, future in enumerate(futures):
            outputs[index] = future.result()
    return outputs
