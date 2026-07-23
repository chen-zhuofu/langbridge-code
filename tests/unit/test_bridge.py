import io
import json
import threading

import pytest

from langbridge_code.ui.bridge import BridgeServer, strip_bug_status


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr("langbridge_code.util.artifacts.ARTIFACTS_DIR", tmp_path)
    out = io.StringIO()
    bridge = BridgeServer(api_key="test-key", model="test-model", out=out)
    bridge._events = lambda: [
        json.loads(line) for line in out.getvalue().splitlines() if line.strip()
    ]
    yield bridge
    bridge.close()


def events_of_type(server, kind):
    return [event for event in server._events() if event["type"] == kind]


def test_strip_bug_status():
    assert strip_bug_status("done\nBUG_STATUS: FIXED") == "done"
    assert strip_bug_status("no token here") == "no token here"


def test_hello_reports_model_and_sessions(server):
    server.hello()
    hello = events_of_type(server, "hello")[0]
    assert hello["model"] == "test-model"
    assert isinstance(hello["sessions"], list)
    assert events_of_type(server, "state")


def test_quit_returns_true(server):
    assert server.handle({"type": "quit"}) is True


def test_user_message_queues_while_turn_active(server):
    server.turn_active = True
    server.handle({"type": "user_message", "text": "next task"})
    queued = events_of_type(server, "queued")
    assert queued and queued[0]["text"] == "next task"
    assert len(server.message_queue) == 1


def test_unknown_skill_slash_does_not_start_turn(server):
    server.handle({"type": "user_message", "text": "/not-a-real-skill"})
    assert server.turn_active is False
    warns = [
        event
        for event in events_of_type(server, "system")
        if "Unknown command or skill" in event.get("text", "")
    ]
    assert warns


def test_known_skill_slash_starts_turn(server, monkeypatch):
    started = []

    def fake_begin(text, *, announce=False):
        started.append(text)

    monkeypatch.setattr(server, "begin_turn", fake_begin)
    server.handle({"type": "user_message", "text": "/grilling focus on auth"})
    assert started == ["/grilling focus on auth"]


def test_queue_list_and_clear(server):
    server.turn_active = True
    server.handle({"type": "user_message", "text": "a"})
    server.handle({"type": "queue_list"})
    queue_events = events_of_type(server, "queue")
    assert queue_events and queue_events[-1]["items"] == ["a"]
    server.handle({"type": "queue_clear"})
    assert len(server.message_queue) == 0


def test_approval_roundtrip(server):
    result = {}

    def requester():
        result["approved"] = server.request_approval("Worker", "bash", {"command": "ls"})

    thread = threading.Thread(target=requester)
    thread.start()
    for _ in range(100):
        if server.pending_approval is not None:
            break
        thread.join(timeout=0.01)
    request = events_of_type(server, "approval_request")[0]
    assert "bash" in request["summary"]
    server.handle({"type": "approval", "approved": True})
    thread.join(timeout=2)
    assert result["approved"] is True
    assert events_of_type(server, "approval_resolved")[0]["approved"] is True


def test_yolo_auto_approves(server):
    server.handle({"type": "yolo", "value": True})
    assert server.always_approve is True
    assert server.request_approval("Worker", "write", {"path": "x"}) is True


def test_question_roundtrip(server):
    result = {}

    def asker():
        result["answer"] = server.request_user_answer("Which one?", ["red", "blue"])

    thread = threading.Thread(target=asker)
    thread.start()
    for _ in range(100):
        if server.pending_question is not None:
            break
        thread.join(timeout=0.01)
    question = events_of_type(server, "question")[0]
    assert "Which one?" in question["text"]
    server.handle({"type": "answer", "text": "1"})
    thread.join(timeout=2)
    assert result["answer"] == "red"


def test_resume_session_replays_conversation(server, tmp_path):
    from langbridge_code.util.session_traces import append_raw_round

    session_dir = tmp_path / "session-demo-2026-01-01T000000"
    session_dir.mkdir()
    append_raw_round(
        session_dir,
        1,
        [
            {"role": "user", "content": "hi there"},
            {"type": "function_call", "name": "read_file", "call_id": "c1", "arguments": "{}"},
            {"role": "assistant", "content": "hello back"},
        ],
    )
    server.handle({"type": "resume_session", "path": str(session_dir)})
    resumed = events_of_type(server, "session_resumed")[0]
    assert resumed["conversation"] == [
        {"role": "user", "text": "hi there"},
        {"role": "assistant", "text": "hello back"},
    ]


def test_new_session_resets(server):
    server.turn_id = 5
    server.handle({"type": "new_session"})
    assert server.turn_id == 0
    assert server.main_agent is None
    assert events_of_type(server, "session_new")


def test_new_session_blocked_while_busy(server):
    server.turn_active = True
    server.handle({"type": "new_session"})
    systems = events_of_type(server, "system")
    assert any("busy" in event["text"] for event in systems)


def test_stop_without_turn_is_noop(server):
    server.handle({"type": "stop"})
    assert server.state == "ready"


def test_trace_event_forwarded(server):
    class Event:
        role = "Explore"
        kind = "action"
        text = "grep(foo)"

    server._trace_event(Event())
    trace = events_of_type(server, "trace")[0]
    assert trace["role"] == "Explore"
    assert trace["text"] == "grep(foo)"


def test_stream_event_forwarded(server):
    class Event:
        role = "LangBridge"
        kind = "content_stream"
        text = "partial reply"

    server._trace_event(Event())
    stream = events_of_type(server, "stream")[0]
    assert stream["text"] == "partial reply"
