"""Eval telemetry — collect model/tool/agent timings during an eval run.

Enabled in the eval subprocess via ``telemetry.start()``. Hooks in the LLM
client and tool runner write here when a collector is active. The subprocess
returns the snapshot in its JSON result for the parent grader.

Progressive disclosure in ``snapshot()``:

- ``events`` — aggregated by model agent label / tool name (count, totals,
  averages). No per-invocation list. This is what the report JSON keeps.
- ``detail_events`` — same grouping, but each group includes a ``calls`` list
  with every individual invocation (plus ``elapsed_s`` from task start).
  Peeled into ``*-report-detail.json`` by ``metrics.record_result``.

All latency fields are in seconds.
"""
from __future__ import annotations

import statistics
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelCallRecord:
    agent: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    time_to_first_token_s: float | None = None
    total_latency_s: float = 0.0
    elapsed_s: float | None = None


@dataclass
class ToolCallRecord:
    tool: str
    latency_s: float
    arguments: dict[str, Any] = field(default_factory=dict)
    agent: str = ""
    elapsed_s: float | None = None


@dataclass
class AgentTaskRecord:
    role: str
    latency_s: float
    task: str = ""


@dataclass
class EvalTelemetry:
    started_at: float = field(default_factory=time.perf_counter)
    model_calls: list[ModelCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    agent_tasks: list[AgentTaskRecord] = field(default_factory=list)
    test_latency_s: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _elapsed_s(self) -> float:
        return time.perf_counter() - self.started_at

    def add_model_call(self, record: ModelCallRecord) -> None:
        with self._lock:
            if record.elapsed_s is None:
                record.elapsed_s = self._elapsed_s()
            self.model_calls.append(record)

    def add_tool_call(self, record: ToolCallRecord) -> None:
        with self._lock:
            if record.elapsed_s is None:
                record.elapsed_s = self._elapsed_s()
            self.tool_calls.append(record)

    def add_agent_task(self, record: AgentTaskRecord) -> None:
        with self._lock:
            self.agent_tasks.append(record)

    def set_test_latency_s(self, value: float) -> None:
        with self._lock:
            self.test_latency_s = value

    def end_to_end_s(self) -> float:
        return self._elapsed_s()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            model_calls = list(self.model_calls)
            tool_calls = list(self.tool_calls)
            agent_tasks = [
                {
                    "role": a.role,
                    "latency_s": _round_s(a.latency_s),
                    "task": a.task,
                }
                for a in self.agent_tasks
            ]
            test_s = self.test_latency_s
            e2e_s = self.end_to_end_s()

        summary_events, detail_events = _build_events(model_calls, tool_calls)

        latencies = [c.total_latency_s for c in model_calls]
        ttfts = [
            c.time_to_first_token_s
            for c in model_calls
            if c.time_to_first_token_s is not None
        ]
        return {
            "events": summary_events,
            "detail_events": detail_events,
            "agent_tasks": agent_tasks,
            "aggregates": {
                "end_to_end_task_latency_s": _round_s(e2e_s),
                "total_model_latency_s": _round_s(sum(latencies)) if latencies else 0.0,
                "avg_model_call_latency_s": (
                    _round_s(statistics.mean(latencies)) if latencies else None
                ),
                "p95_model_call_latency_s": (
                    _round_s(_p95(latencies)) if latencies else None
                ),
                "avg_time_to_first_token_s": (
                    _round_s(statistics.mean(ttfts)) if ttfts else None
                ),
                "total_tool_latency_s": _round_s(sum(t.latency_s for t in tool_calls)),
                "total_test_latency_s": _round_s(test_s) if test_s is not None else None,
                "coder_task_latencies_s": [
                    a["latency_s"] for a in agent_tasks if a["role"] == "coder"
                ],
                "reviewer_task_latencies_s": [
                    a["latency_s"] for a in agent_tasks if a["role"] == "reviewer"
                ],
            },
        }


def _avg(values: list[float | int]) -> float | None:
    if not values:
        return None
    return statistics.mean(values)


def _build_events(
    model_calls: list[ModelCallRecord],
    tool_calls: list[ToolCallRecord],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (summary_events, detail_events). Same grouping; detail has calls."""
    model_groups: dict[str, list[ModelCallRecord]] = {}
    model_order: list[str] = []
    for call in model_calls:
        key = call.agent or "unknown"
        if key not in model_groups:
            model_groups[key] = []
            model_order.append(key)
        model_groups[key].append(call)

    tool_groups: dict[str, list[ToolCallRecord]] = {}
    tool_order: list[str] = []
    for call in tool_calls:
        key = call.tool or "unknown"
        if key not in tool_groups:
            tool_groups[key] = []
            tool_order.append(key)
        tool_groups[key].append(call)

    summary: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []

    for agent in model_order:
        group = model_groups[agent]
        latencies = [c.total_latency_s for c in group]
        ttfts = [c.time_to_first_token_s for c in group if c.time_to_first_token_s is not None]
        in_tok = [c.input_tokens for c in group if c.input_tokens is not None]
        out_tok = [c.output_tokens for c in group if c.output_tokens is not None]
        base = {
            "type": "model_call",
            "agent": agent,
            "count": len(group),
            "avg_input_tokens": _round_num(_avg(in_tok)),
            "avg_output_tokens": _round_num(_avg(out_tok)),
            "total_latency_s": _round_s(sum(latencies)),
            "avg_latency_s": _round_s(_avg(latencies)),
            "avg_time_to_first_token_s": _round_s(_avg(ttfts)),
        }
        summary.append(dict(base))
        detail.append(
            {
                **base,
                "calls": [
                    {
                        "elapsed_s": _round_s(c.elapsed_s),
                        "latency_s": _round_s(c.total_latency_s),
                        "input_tokens": c.input_tokens,
                        "output_tokens": c.output_tokens,
                        "time_to_first_token_s": _round_s(c.time_to_first_token_s),
                    }
                    for c in group
                ],
            }
        )

    for tool in tool_order:
        group = tool_groups[tool]
        latencies = [c.latency_s for c in group]
        base = {
            "type": "tool_call",
            "tool": tool,
            "count": len(group),
            "total_latency_s": _round_s(sum(latencies)),
            "avg_latency_s": _round_s(_avg(latencies)),
        }
        summary.append(dict(base))
        detail.append(
            {
                **base,
                "calls": [
                    {
                        "elapsed_s": _round_s(c.elapsed_s),
                        "latency_s": _round_s(c.latency_s),
                        "agent": c.agent,
                        "arguments": c.arguments,
                    }
                    for c in group
                ],
            }
        )

    return summary, detail


def split_report_and_detail_telemetry(telemetry: dict | None) -> tuple[dict, list | None]:
    """Return (report_telemetry, detail_events_or_none).

    Report keeps aggregates / summary events / agent_tasks. Detail is only the
    per-invocation model+tool event list (or None if absent).
    """
    if not telemetry:
        return {}, None
    report = dict(telemetry)
    detail = report.pop("detail_events", None)

    events = report.get("events")
    if isinstance(events, list):
        cleaned = []
        legacy_detail = []
        for e in events:
            if not isinstance(e, dict):
                cleaned.append(e)
                continue
            row = dict(e)
            calls = row.pop("calls", None)
            cleaned.append(row)
            if not calls:
                continue
            legacy_detail.append({**row, "calls": calls})
        report["events"] = cleaned
        if detail is None and legacy_detail:
            detail = legacy_detail
    return report, detail


def _round_s(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 3)


def _round_num(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 1)


def _p95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return float(statistics.quantiles(values, n=20)[18])


_active: ContextVar[EvalTelemetry | None] = ContextVar("eval_telemetry", default=None)


def get_telemetry() -> EvalTelemetry | None:
    return _active.get()


@contextmanager
def start_telemetry():
    tel = EvalTelemetry()
    token = _active.set(tel)
    try:
        yield tel
    finally:
        _active.reset(token)


@contextmanager
def telemetry_scope(tel: EvalTelemetry | None):
    """Install ``tel`` on this thread (for ThreadPool workers).

    ContextVars do not follow ThreadPoolExecutor threads automatically; bind the
    parent collector explicitly so Worker/Explorer tool and model calls still
    record into the same eval snapshot.
    """
    if tel is None:
        yield
        return
    token = _active.set(tel)
    try:
        yield
    finally:
        _active.reset(token)


def record_model_call(**kwargs) -> None:
    tel = get_telemetry()
    if tel is None:
        return
    kwargs.pop("t_ms", None)
    kwargs.pop("t_s", None)
    tel.add_model_call(ModelCallRecord(**kwargs))


def record_tool_call(tool: str, latency_s: float, arguments=None, agent: str = "") -> None:
    tel = get_telemetry()
    if tel is None:
        return
    tel.add_tool_call(
        ToolCallRecord(
            tool=tool,
            latency_s=latency_s,
            arguments=dict(arguments or {}),
            agent=agent,
        )
    )


def record_agent_task(role: str, latency_s: float, task: str = "") -> None:
    tel = get_telemetry()
    if tel is None:
        return
    tel.add_agent_task(AgentTaskRecord(role=role, latency_s=latency_s, task=task[:500]))


@contextmanager
def timed_agent_task(role: str, task: str = ""):
    if get_telemetry() is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        record_agent_task(role=role, latency_s=time.perf_counter() - start, task=task)
