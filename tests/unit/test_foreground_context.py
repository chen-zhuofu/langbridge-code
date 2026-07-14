"""Foreground context tracking for status-bar display."""
from langbridge_code.context.foreground import (
    ForegroundTracker,
    clear_foreground,
    current_foreground,
    enter_foreground,
    leave_foreground,
    publish_foreground,
)


def test_current_foreground_prefers_most_recent_publish():
    clear_foreground()
    messages_a = [{"role": "system", "content": "a"}]
    messages_b = [{"role": "system", "content": "b"}]
    token_a = enter_foreground("Explore", messages_a, "model-a")
    token_b = enter_foreground("Worker", messages_b, "model-b")
    publish_foreground(token_a, messages_a, "model-a")
    publish_foreground(token_b, messages_b, "model-b")

    snap = current_foreground()
    assert snap is not None
    assert snap.label == "Worker"
    assert snap.model == "model-b"

    leave_foreground(token_b)
    snap = current_foreground()
    assert snap is not None
    assert snap.label == "Explore"

    leave_foreground(token_a)
    assert current_foreground() is None


def test_foreground_tracker_scope():
    clear_foreground()
    messages = [{"role": "system", "content": "main"}]
    tracker = ForegroundTracker("LangBridge", messages, "kimi")
    tracker.activate()
    tracker.publish()
    assert current_foreground().label == "LangBridge"
    tracker.deactivate()
    assert current_foreground() is None
