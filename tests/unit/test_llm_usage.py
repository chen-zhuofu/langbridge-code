import json

from langbridge_code.agents.common import control
from langbridge_code.llm.usage import (
    cache_hit_rate,
    format_usage_line,
    normalize_usage,
    record_usage,
)
from langbridge_code.util.trace_log import TraceContext, set_trace_context


def test_normalize_usage_openai_cached_details():
    usage = normalize_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 800},
        }
    )
    assert usage == {
        "input_tokens": 1000,
        "output_tokens": 40,
        "cached_tokens": 800,
        "cache_write_tokens": 0,
        "cache_reported": True,
    }
    assert cache_hit_rate(1000, 800) == 0.8


def test_normalize_usage_deepseek_hit_tokens():
    usage = normalize_usage(
        {
            "prompt_tokens": 500,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 400,
            "prompt_cache_miss_tokens": 100,
        }
    )
    assert usage["cached_tokens"] == 400
    assert usage["cache_write_tokens"] == 0


def test_normalize_usage_anthropic_style_fields():
    usage = normalize_usage(
        {
            "input_tokens": 2000,
            "output_tokens": 50,
            "cache_read_input_tokens": 1500,
            "cache_creation_input_tokens": 200,
        }
    )
    assert usage["cached_tokens"] == 1500
    assert usage["cache_write_tokens"] == 200


def test_normalize_usage_marks_missing_cache_fields_unknown():
    usage = normalize_usage({"prompt_tokens": 500, "completion_tokens": 20})

    assert usage["cached_tokens"] == 0
    assert usage["cache_reported"] is False


def test_format_usage_line_includes_session_hit():
    line = format_usage_line(
        {"input_tokens": 100, "output_tokens": 10, "cached_tokens": 50},
        session={"input_tokens": 300, "cached_tokens": 150, "calls": 3},
    )
    assert "hit=50.0%" in line
    assert "session_hit=50.0% (150/300)" in line
    assert "calls=3" in line


def test_record_usage_writes_session_totals(tmp_path):
    set_trace_context(TraceContext(run_log_path=tmp_path, trace_id="t1"))
    try:
        totals = record_usage(
            {
                "input_tokens": 100,
                "output_tokens": 5,
                "cached_tokens": 40,
                "cache_write_tokens": 0,
            },
            label="LangBridge",
        )
        totals2 = record_usage(
            {
                "input_tokens": 100,
                "output_tokens": 5,
                "cached_tokens": 80,
                "cache_write_tokens": 0,
            },
            label="LangBridge",
        )
    finally:
        set_trace_context(None)

    assert totals["calls"] == 1
    assert totals2["calls"] == 2
    assert totals2["input_tokens"] == 200
    assert totals2["cached_tokens"] == 120
    assert totals2["cache_hit_rate"] == 0.6
    assert totals2["calls_with_cache_data"] == 2
    payload = json.loads((tmp_path / "llm_usage.json").read_text(encoding="utf-8"))
    assert payload["cache_hit_rate"] == 0.6
    session = (tmp_path / "session.md").read_text(encoding="utf-8")
    assert "usage: in=100" in session
    assert "session_hit=" in session


def test_interruptible_llm_thread_preserves_usage_trace_context(tmp_path):
    control.clear_stop()
    set_trace_context(TraceContext(run_log_path=tmp_path, trace_id="t1"))
    try:
        control.run_interruptible(
            lambda: record_usage(
                {
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "cached_tokens": 25,
                    "cache_write_tokens": 0,
                    "cache_reported": True,
                },
                label="LangBridge",
            )
        )
    finally:
        set_trace_context(None)

    payload = json.loads((tmp_path / "llm_usage.json").read_text(encoding="utf-8"))
    assert payload["cache_hit_rate"] == 0.25
