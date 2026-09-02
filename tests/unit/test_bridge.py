import io
import json
import subprocess
import threading
from pathlib import Path

import pytest

from langbridge_code.agents.common import control
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
    assert any(item["name"] == "creating-skills" for item in hello["skills"])
    assert events_of_type(server, "state")


def test_new_session_path_is_published_and_reused(server, monkeypatch):
    from langbridge_code.util.session_traces import read_conversation

    class NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", NoopThread)

    server.begin_turn("first message")
    first_path = server.run_log_path
    assert read_conversation(first_path) == [("user", "first message")]
    server.turn_active = False
    server.begin_turn("second message")

    assert server.run_log_path == first_path
    assert events_of_type(server, "state")[-1]["session_path"] == str(first_path)
    session_events = events_of_type(server, "sessions")
    assert len(session_events) == 1
    assert session_events[0]["items"] == [
        {"path": str(first_path), "label": first_path.name.removeprefix("session-")}
    ]


def test_session_labels_hide_storage_prefix(server, tmp_path):
    session = tmp_path / "session-fix-login-2026-08-28T120000"
    session.mkdir()
    server.session_logs = [session]

    assert server._session_items() == [
        {"path": str(session), "label": "fix-login-2026-08-28T120000"}
    ]


def test_rename_session_persists_display_name(server, tmp_path):
    session = tmp_path / "session-fix-login-2026-08-28T120000"
    session.mkdir()
    server.session_logs = [session]

    server.handle(
        {"type": "rename_session", "path": str(session), "title": "  Login   repair  "}
    )

    assert events_of_type(server, "session_renamed")[-1] == {
        "type": "session_renamed",
        "path": str(session.resolve()),
        "label": "Login repair",
        "current": False,
    }
    assert server._session_items() == [
        {"path": str(session), "label": "Login repair"}
    ]


def test_fork_session_copies_context_and_preserves_source(server, tmp_path):
    session = tmp_path / "session-fix-login-2026-08-28T120000"
    session.mkdir()
    (session / "session_memory.md").write_text("# Session memory\nkey fact\n", encoding="utf-8")
    (session / "traces.md").write_text("# Session traces\nturn 1\n", encoding="utf-8")
    server.run_log_path = session
    server.turn_active = False
    server.main_agent = type(
        "Agent",
        (),
        {
            "context": type(
                "Context",
                (),
                {"stack": type("Stack", (), {"memory_block": "selected memory"})()},
            )()
        },
    )()

    server.handle({"type": "fork_session"})

    forked = events_of_type(server, "session_forked")[-1]
    assert forked["path"] != str(session)
    assert "fork" in forked["label"].lower()

    forked_dir = Path(forked["path"])
    assert forked_dir.is_dir()
    assert (forked_dir / "session_memory.md").read_text(encoding="utf-8") == "# Session memory\nkey fact\n"
    assert (forked_dir / "traces.md").read_text(encoding="utf-8") == "# Session traces\nturn 1\n"
    assert (forked_dir / ".fork-memory-context.md").read_text(encoding="utf-8") == "selected memory"

    # Source session is untouched.
    assert (session / "session_memory.md").read_text(encoding="utf-8") == "# Session memory\nkey fact\n"
    assert server.run_log_path == session

    assert events_of_type(server, "sessions")[-1]["items"]


def test_fork_session_rejects_blank_session(server):
    server.run_log_path = None

    server.handle({"type": "fork_session"})

    systems = events_of_type(server, "system")
    assert any("before forking" in event["text"] for event in systems)
    assert not events_of_type(server, "session_forked")


def test_fork_session_rejects_busy_session(server, tmp_path):
    session = tmp_path / "session-fix-login-2026-08-28T120000"
    session.mkdir()
    server.run_log_path = session
    server.turn_active = True

    server.handle({"type": "fork_session"})

    systems = events_of_type(server, "system")
    assert any("busy" in event["text"] for event in systems)
    assert not events_of_type(server, "session_forked")


def test_list_skills_refreshes_catalog(server, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.skills.list_skills",
        lambda role=None: [("new-skill", "Newly created skill")],
    )

    server.handle({"type": "list_skills"})

    assert events_of_type(server, "skills")[-1]["items"] == [
        {"name": "new-skill", "description": "Newly created skill"}
    ]


def test_context_line_reports_cache_hit_rate(server, tmp_path):
    server.run_log_path = tmp_path
    (tmp_path / "llm_usage.json").write_text(
        json.dumps(
            {
                "input_tokens": 1_000,
                "cached_tokens": 730,
                "calls_with_cache_data": 2,
                "cache_hit_rate": 0.73,
            }
        ),
        encoding="utf-8",
    )

    server.push_context_line(force=True)

    event = events_of_type(server, "context_line")[-1]
    assert event["cache_available"] is True
    assert event["cache_hit_rate"] == 0.73


def test_context_line_always_reports_numeric_cache_rate(server):
    server.push_context_line(force=True)

    event = events_of_type(server, "context_line")[-1]
    assert event["cache_available"] is False
    assert event["cache_hit_rate"] == 0.0


def test_resume_forces_cache_rate_refresh_after_initial_context_line(server, tmp_path):
    session = tmp_path / "session-resume-cache"
    session.mkdir()
    (session / "llm_usage.json").write_text(
        json.dumps(
            {
                "input_tokens": 1_000,
                "cached_tokens": 730,
                "calls_with_cache_data": 2,
                "cache_hit_rate": 0.73,
            }
        ),
        encoding="utf-8",
    )
    server.push_context_line(force=True)
    initial_count = len(events_of_type(server, "context_line"))

    server.resume_session(str(session))

    context_events = events_of_type(server, "context_line")
    assert len(context_events) == initial_count + 1
    assert context_events[-1]["cache_hit_rate"] == 0.73



def test_quit_returns_true(server):
    assert server.handle({"type": "quit"}) is True


def test_close_drains_browser_worker(server, monkeypatch):
    calls = []
    monkeypatch.setattr("langbridge_code.ui.bridge.shutdown_browser", lambda: calls.append("stop"))

    server.close()

    assert calls == ["stop"]
    control.checkpoint()


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


def test_user_message_passes_validated_image_paths(server, monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    started = []

    def fake_begin(text, *, image_paths=None, announce=False):
        started.append((text, image_paths, announce))

    monkeypatch.setattr(server, "begin_turn", fake_begin)
    server.handle(
        {
            "type": "user_message",
            "text": "What is this?",
            "images": [{"path": str(image)}],
        }
    )
    assert started == [("What is this?", [str(image.resolve())], False)]


def test_image_only_message_starts_without_synthetic_text(server, monkeypatch, tmp_path):
    from langbridge_code.util.session_traces import read_conversation_items

    class NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", NoopThread)
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")

    server.handle(
        {
            "type": "user_message",
            "text": "",
            "images": [{"path": str(image)}],
        }
    )

    assert read_conversation_items(server.run_log_path) == [
        {
            "role": "user",
            "text": "",
            "turn_id": 1,
            "images": [str(image.resolve())],
        }
    ]


def test_invalid_image_reports_error_without_starting_turn(server, tmp_path):
    server.handle(
        {
            "type": "user_message",
            "text": "inspect",
            "images": [{"path": str(tmp_path / "missing.png")}],
        }
    )
    assert server.turn_active is False
    assert "Image attachment error" in events_of_type(server, "system")[-1]["text"]


def test_list_and_set_model(server, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "langbridge_code.ui.bridge.list_model_catalog",
        lambda api_key: [
            {"id": "model-a", "provider": "deepseek"},
            {"id": "model-b", "provider": "moonshot"},
        ],
    )
    saved = []

    def fake_set(model, *, provider=None):
        saved.append((model, provider))
        return model

    monkeypatch.setattr("langbridge_code.ui.bridge.set_default_model", fake_set)
    monkeypatch.setattr(
        "langbridge_code.ui.bridge.load_api_key",
        lambda provider=None: "test-key",
    )
    server.handle({"type": "list_models"})
    models = events_of_type(server, "models")[-1]
    assert models["items"][0]["id"] == "test-model"
    assert {item["id"] for item in models["items"]} >= {"test-model", "model-a", "model-b"}
    assert models["current"] == "test-model"

    server.handle({"type": "set_model", "model": "model-b", "provider": "moonshot"})
    assert server.model == "model-b"
    assert saved == [("model-b", "moonshot")]
    assert events_of_type(server, "model")[-1]["model"] == "model-b"


def test_set_model_blocked_while_turn_active(server, monkeypatch):
    monkeypatch.setattr("langbridge_code.ui.bridge.set_default_model", lambda model: model)
    server.turn_active = True
    server.handle({"type": "set_model", "model": "other"})
    assert server.model == "test-model"
    warns = [
        event
        for event in events_of_type(server, "system")
        if "busy" in event.get("text", "").lower()
    ]
    assert warns


def test_reload_credentials_updates_busy_existing_session(server, monkeypatch):
    class FakeAgent:
        api_key = "old-key"
        model = "old-model"
        rebuilds = 0

        def _rebuild_subagent_tools(self):
            self.rebuilds += 1

    monkeypatch.setattr(
        "langbridge_code.ui.bridge.reload_runtime_credentials",
        lambda: ("moonshot", "new-key", "new-model"),
    )
    server.main_agent = FakeAgent()
    server.turn_active = True

    server.handle({"type": "reload_credentials"})

    assert server.api_key == "new-key"
    assert server.model == "new-model"
    assert server.main_agent.api_key == "new-key"
    assert server.main_agent.model == "new-model"
    assert server.main_agent.rebuilds == 1
    event = events_of_type(server, "credentials_reloaded")[-1]
    assert event == {
        "type": "credentials_reloaded",
        "provider": "moonshot",
        "model": "new-model",
    }
    assert "new-key" not in json.dumps(event)


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
    assert server.permission_mode == "bypass"
    assert server.request_approval("Worker", "write", {"path": "x"}) is True


def test_auto_mode_uses_classifier_for_shell_actions(server, monkeypatch):
    decisions = []

    def classify(_self, messages, role, tool_name, arguments):
        from langbridge_code.agents.common.auto_mode import AutoModeDecision

        decisions.append((messages, role, tool_name, arguments))
        return AutoModeDecision(False, 2, "remote deletion was not authorized")

    monkeypatch.setattr(
        "langbridge_code.ui.bridge.AutoModeClassifier.evaluate",
        classify,
    )
    server.handle({"type": "permission_mode", "value": "auto"})

    assert server.request_approval(
        "LangBridge",
        "bash",
        {"command": "git push origin --delete old"},
    ) is False
    assert decisions and decisions[0][2] == "bash"
    assert "remote deletion" in events_of_type(server, "system")[-1]["text"]
    assert events_of_type(server, "state")[-1]["permission_mode"] == "auto"


def test_auto_mode_skips_classifier_for_project_edits(server, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.ui.bridge.AutoModeClassifier.evaluate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("project edits must skip the classifier")
        ),
    )
    server.handle({"type": "permission_mode", "value": "auto"})

    assert server.request_approval("Worker", "write", {"path": "src/app.py"}) is True


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
        {"role": "user", "text": "hi there", "turn_id": 1, "checkpoint_available": False},
        {"role": "assistant", "text": "hello back", "turn_id": 1, "checkpoint_available": False},
    ]


def test_resume_session_replays_image_paths(server, tmp_path):
    from langbridge_code.util.session_traces import append_raw_round

    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    session_dir = tmp_path / "session-images"
    session_dir.mkdir()
    append_raw_round(
        session_dir,
        1,
        [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "inspect"},
                    {"type": "input_image", "image_path": str(image)},
                ],
            },
            {"role": "assistant", "content": "done"},
        ],
    )
    server.handle({"type": "resume_session", "path": str(session_dir)})
    resumed = events_of_type(server, "session_resumed")[0]
    assert resumed["conversation"][0] == {
        "role": "user",
        "text": "inspect",
        "turn_id": 1,
        "checkpoint_available": False,
        "images": [str(image)],
    }


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


class _NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


def _init_git_repo(root):
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test",
         "commit", "--allow-empty", "-m", "init"],
        cwd=root, check=True, capture_output=True,
    )


def test_begin_turn_creates_checkpoint_before_mutating_workspace(server, tmp_path, monkeypatch):
    from langbridge_code.util import checkpoints as ckpt

    repo = tmp_path / "workspace"
    repo.mkdir()
    _init_git_repo(repo)
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", repo)
    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", _NoopThread)

    server.begin_turn("first message")

    assert ckpt.checkpoint_available(server.run_log_path, 1) is True


def test_begin_turn_always_emits_turn_id_assigned_without_announce(server, tmp_path, monkeypatch):
    from langbridge_code.util import checkpoints as ckpt

    repo = tmp_path / "workspace"
    repo.mkdir()
    _init_git_repo(repo)
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", repo)
    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", _NoopThread)

    server.begin_turn("hello")

    assigned = events_of_type(server, "turn_id_assigned")
    assert assigned and assigned[-1] == {
        "type": "turn_id_assigned",
        "turn_id": 1,
        "checkpoint_available": True,
    }
    assert events_of_type(server, "turn_started") == []


def test_begin_turn_announce_includes_turn_id_on_turn_started(server, tmp_path, monkeypatch):
    from langbridge_code.util import checkpoints as ckpt

    repo = tmp_path / "workspace"
    repo.mkdir()
    _init_git_repo(repo)
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", repo)
    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", _NoopThread)

    server.begin_turn("queued message", announce=True)

    started = events_of_type(server, "turn_started")[-1]
    assert started["turn_id"] == 1
    assert started["checkpoint_available"] is True
    assert events_of_type(server, "turn_id_assigned") == []


def test_checkpoint_failure_does_not_crash_begin_turn(server, monkeypatch):
    monkeypatch.setattr(
        "langbridge_code.ui.bridge.create_checkpoint",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", _NoopThread)

    server.begin_turn("first message")

    assert server.run_log_path is not None
    assigned = events_of_type(server, "turn_id_assigned")
    assert assigned and assigned[-1]["checkpoint_available"] is False


def test_rewind_rejected_while_turn_active(server):
    server.turn_active = True

    server.handle({"type": "rewind_to_turn", "turn_id": 1})

    assert events_of_type(server, "rewound") == []
    warns = [e for e in events_of_type(server, "system") if "busy" in e.get("text", "").lower()]
    assert warns


def test_rewind_rejected_with_no_active_session(server):
    server.handle({"type": "rewind_to_turn", "turn_id": 1})

    assert events_of_type(server, "rewound") == []
    warns = [e for e in events_of_type(server, "system") if e.get("style") == "warn"]
    assert warns


def test_rewind_rejected_for_unknown_turn(server, tmp_path, monkeypatch):
    from langbridge_code.util import checkpoints as ckpt

    repo = tmp_path / "workspace"
    repo.mkdir()
    _init_git_repo(repo)
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", repo)
    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", _NoopThread)

    server.begin_turn("only message")
    server.turn_active = False

    server.handle({"type": "rewind_to_turn", "turn_id": 99})

    assert events_of_type(server, "rewound") == []


def test_rewind_rejected_when_checkpoint_unavailable(server, tmp_path, monkeypatch):
    from langbridge_code.util import checkpoints as ckpt

    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", not_a_repo)
    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", _NoopThread)

    server.begin_turn("only message")
    server.turn_active = False

    server.handle({"type": "rewind_to_turn", "turn_id": 1})

    assert events_of_type(server, "rewound") == []
    warns = [e for e in events_of_type(server, "system") if "checkpoint" in e.get("text", "").lower()]
    assert warns


def test_rewind_restores_workspace_and_truncates_conversation(server, tmp_path, monkeypatch):
    from langbridge_code.util import checkpoints as ckpt

    repo = tmp_path / "workspace"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "app.py").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test",
         "commit", "-m", "seed"],
        cwd=repo, check=True, capture_output=True,
    )
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", repo)
    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", _NoopThread)

    server.begin_turn("turn one")
    server.turn_active = False
    (repo / "app.py").write_text("v2 changed by agent\n", encoding="utf-8")
    server.begin_turn("turn two")
    server.turn_active = False
    (repo / "app.py").write_text("v3 changed by agent\n", encoding="utf-8")

    server.main_agent = object()
    server.session_goal = object()

    server.handle({"type": "rewind_to_turn", "turn_id": 2})

    assert (repo / "app.py").read_text(encoding="utf-8") == "v2 changed by agent\n"
    rewound = events_of_type(server, "rewound")[-1]
    assert rewound["turn_id"] == 2
    assert rewound["conversation"] == [
        {"role": "user", "text": "turn one", "turn_id": 1, "checkpoint_available": True},
    ]
    assert server.turn_id == 1
    assert server.main_agent is None
    assert server.session_goal is None


def test_rewind_failure_reports_error_and_makes_no_changes(server, tmp_path, monkeypatch):
    from langbridge_code.util import checkpoints as ckpt

    repo = tmp_path / "workspace"
    repo.mkdir()
    _init_git_repo(repo)
    monkeypatch.setattr(ckpt, "WORKSPACE_ROOT", repo)
    monkeypatch.setattr("langbridge_code.ui.bridge.threading.Thread", _NoopThread)

    server.begin_turn("only message")
    server.turn_active = False
    monkeypatch.setattr(
        "langbridge_code.ui.bridge.restore_checkpoint",
        lambda *a, **k: ckpt.RestoreResult(False, "simulated failure"),
    )

    server.handle({"type": "rewind_to_turn", "turn_id": 1})

    assert events_of_type(server, "rewound") == []
    errors = [e for e in events_of_type(server, "system") if e.get("style") == "error"]
    assert errors and "simulated failure" in errors[-1]["text"]
