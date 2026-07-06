from langbridge_code.workflow.phases import WorkflowPhase, emit_phase


def test_emit_phase_calls_sink():
    seen = []
    emit_phase(seen.append, "routing")
    assert seen == [WorkflowPhase(step="routing")]


def test_emit_phase_noop_without_sink():
    emit_phase(None, "planning")
