"""Per-session workflow phase notifications for the TUI streaming state machine."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowPhase:
    step: str  # planning | coding | reviewing | presenting | refining | exploring | summarizing | evaluating


def emit_phase(phase_sink, step: str) -> None:
    if phase_sink is None:
        return
    phase_sink(WorkflowPhase(step=step))
