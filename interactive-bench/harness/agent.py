"""Pluggable coding-agent backends for the interactive eval harness.

Stub: in-process fake agent (tests / wiring).
Docker: LangBridge main agent inside ``lb-interactive:<id>``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

AgentTurnFn = Callable[[str], dict[str, Any]]


def make_stub_agent(*, max_agent_turns: int = 2) -> AgentTurnFn:
    counter = {"n": 0}

    def agent_turn(user_text: str) -> dict[str, Any]:
        counter["n"] += 1
        if counter["n"] >= max_agent_turns:
            return {"assistant_text": "All done. Tests pass.", "done": True}
        return {
            "assistant_text": f"Working on it (got: {user_text[:80]})",
            "done": False,
        }

    return agent_turn


def make_docker_agent(
    spec: dict,
    *,
    artifacts_dir: Path | None = None,
    turn_timeout_sec: int = 900,
    model: str | None = None,
):
    """Return a Docker-backed main-agent turn callable (see ``docker_main_agent``)."""
    from harness.docker_main_agent import make_docker_agent as _make

    return _make(
        spec,
        artifacts_dir=artifacts_dir,
        turn_timeout_sec=turn_timeout_sec,
        model=model,
    )
