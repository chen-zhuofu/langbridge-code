"""Shared test setup: keep the suite hermetic (no real LLM traffic)."""
from __future__ import annotations

import sys
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sim_llm(monkeypatch):
    """The user simulator must never call a real model from tests.

    Real keys may resolve from the developer's LangBridge config, so relying
    on "no key in the environment" is not safe. Tests that need a specific
    sim decision patch ``harness.sim.chat_json_ex`` themselves; everyone else
    gets an unreachable sim (drives the ``allow_offline`` / SimUnavailable paths).
    """
    import harness.score as score_mod
    import harness.sim as sim_mod

    monkeypatch.setattr(
        sim_mod, "chat_json_ex", lambda **_kw: (None, "no API key (test stub)")
    )
    monkeypatch.setattr(
        score_mod, "chat_json_ex", lambda **_kw: (None, "no API key (test stub)")
    )
