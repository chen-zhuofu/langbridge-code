"""Eval subprocess helpers — plus hermetic seams for prefetch/fork LLM calls.

Sessions prefetch memory and skill indexes (one-pass LLM) and fork note/memory
writers on their live context. Unit tests construct sessions with fake API keys,
so these helpers are stubbed out by default to keep tests offline. Tests that
exercise the real helpers override the ``_no_llm_prefetch`` fixture locally
(and monkeypatch ``create_model_response`` themselves).
"""
import pytest


@pytest.fixture(autouse=True)
def _no_llm_prefetch(monkeypatch):
    import langbridge_code.agents.common.fork as fork_mod
    import langbridge_code.skills as skills_mod
    import langbridge_code.memory as memory_mod

    monkeypatch.setattr(memory_mod, "prefetch_memory", lambda *args, **kwargs: "")
    monkeypatch.setattr(memory_mod, "schedule_memory_extraction", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        skills_mod,
        "select_skill_index",
        lambda api_key, model, task, catalog, **kwargs: (catalog or "").strip(),
    )
    monkeypatch.setattr(fork_mod, "fork_one_pass", lambda *args, **kwargs: "")
    yield
