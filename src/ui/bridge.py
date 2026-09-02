"""Headless JSONL bridge: the agent engine behind the TypeScript TUI.

Protocol: one JSON object per line.
  stdin  (client -> engine): user_message, approval, answer, permission_mode, yolo, pause_toggle,
          stop, new_session, list_sessions, resume_session, delete_session,
          goal, reviewer, rewind_to_turn, queue_list, queue_clear, list_models,
          set_model, list_skills, rename_session, fork_session, reload_credentials, quit
  stdout (engine -> client): hello, system, assistant, turn_started, trace,
          stream, state, context_line, approval_request, question, turn_end,
          sessions, session_resumed, session_renamed, session_forked, queue, models,
          model, skills, credentials_reloaded, turn_id_assigned, rewound

All UI rendering lives in the client; this module only runs turns and reports
events. Replaces the Textual TUI's threading model: events are written to
stdout under a lock, so callbacks may fire from any worker thread.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from langbridge_code import settings
from langbridge_code.agents.common import control
from langbridge_code.agents.main_agent import MainAgentSession
from langbridge_code.prompt.system import langbridge_system_prompt
from langbridge_code.context.common.budget import format_status_context_line
from langbridge_code.context.foreground import (
    clear_foreground,
    current_foreground,
    register_foreground_listener,
    unregister_foreground_listener,
)
from langbridge_code.settings import (
    infer_provider_for_model,
    list_model_catalog,
    load_api_key,
    reload_runtime_credentials,
    set_default_model,
)
from langbridge_code.agents.common.approval import circuit_breaker_reason
from langbridge_code.agents.common.auto_mode import AutoModeClassifier, auto_mode_route
from langbridge_code.tools.common.runtime import RuntimeBootstrapError, bootstrap_runtime
from langbridge_code.tools.browser import shutdown_browser
from langbridge_code.ui.message_queue import QueuedUserMessage, UserMessageQueue
from langbridge_code.llm.images import (
    ImageAttachmentError,
    normalize_image_paths,
)
from langbridge_code.llm.usage import load_usage_totals
from langbridge_code.util.artifacts import artifact_dir, format_trace_timestamp
from langbridge_code.util.checkpoints import (
    checkpoint_available,
    create_checkpoint,
    restore_checkpoint,
)
from langbridge_code.util.goal import (
    STATUS_ACHIEVED,
    STATUS_ACTIVE,
    STATUS_PAUSED,
    build_continuation_prompt,
    clear_goal,
    format_goal_status,
    load_goal,
    new_goal,
    parse_goal_command,
    save_goal,
)
from langbridge_code.util.progress import build_main_agent_messages
from langbridge_code.util.session import (
    create_run_log_path,
    ensure_run_log_path,
    fork_session,
    label_session,
    last_turn_id,
    list_session_logs,
    read_fork_memory_context,
    rename_session,
)
from langbridge_code.util.trace_log import begin_trace, combine_trace_sink, end_trace, trace_sink

_BUG_STATUS_RE = re.compile(r"\s*BUG_STATUS:\s*[A-Za-z]+\s*$", re.IGNORECASE)
_CONTEXT_LINE_MIN_INTERVAL = 1.0


def strip_bug_status(text: str) -> str:
    return _BUG_STATUS_RE.sub("", (text or "").rstrip()).rstrip()


def _version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("langbridge")
        except PackageNotFoundError:
            return "0.1.0"
    except Exception:  # noqa: BLE001
        return "0.1.0"


def _short_cwd() -> str:
    home = str(Path.home())
    cwd = str(Path.cwd())
    return "~" + cwd[len(home) :] if cwd.startswith(home) else cwd


def _git_branch() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""
    return ""


class BridgeServer:
    def __init__(self, api_key=None, model=None, *, out=None):
        control.clear_stop()
        control.resume()
        self.api_key = api_key or load_api_key()
        # Read after load_api_key(): first-run provider selection rebinds DEFAULT_MODEL.
        self.model = model or os.environ.get("LANGBRIDGE_MODEL") or settings.DEFAULT_MODEL
        self._out = out or sys.stdout
        self._out_lock = threading.Lock()
        self.session_logs = list_session_logs()
        self.run_log_path = None
        self.turn_id = 0
        self.messages = [{"role": "system", "content": langbridge_system_prompt()}]
        self.main_agent = None
        self.pending_approval = None
        self.pending_question = None
        self.permission_mode = "manual"
        self.always_approve = False
        self.turn_active = False
        self.state = "ready"
        self.workflow_step = ""
        self.session_goal = None
        self.message_queue = UserMessageQueue()
        self._last_context_line_at = 0.0
        register_foreground_listener(self._on_foreground_change)

    def close(self) -> None:
        had_active_turn = self.turn_active
        unregister_foreground_listener(self._on_foreground_change)
        control.request_stop()
        control.resume()
        if self.pending_approval is not None:
            decision, ready = self.pending_approval
            decision["approved"] = False
            self.pending_approval = None
            ready.set()
        if self.pending_question is not None:
            answer, ready, _ = self.pending_question
            answer["text"] = ""
            self.pending_question = None
            ready.set()
        shutdown_browser()
        if not had_active_turn:
            control.clear_stop()

    # --- transport ----------------------------------------------------------

    def send(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False)
        with self._out_lock:
            self._out.write(line + "\n")
            self._out.flush()

    def system(self, text: str, style: str = "dim") -> None:
        self.send({"type": "system", "text": text, "style": style})

    # --- lifecycle ----------------------------------------------------------

    def hello(self) -> None:
        self.session_logs = list_session_logs()
        self.send(
            {
                "type": "hello",
                "model": self.model,
                "version": _version(),
                "cwd": _short_cwd(),
                "git_branch": _git_branch(),
                "sessions": self._session_items(),
                "skills": self._skill_items(),
            }
        )
        self.push_state()

    def _session_items(self) -> list[dict]:
        return [{"path": str(path), "label": label_session(path)} for path in self.session_logs]

    @staticmethod
    def _skill_items() -> list[dict]:
        from langbridge_code.skills import list_skills

        return [
            {"name": name, "description": description}
            for name, description in list_skills(role="langbridge")
        ]

    def run(self) -> None:
        self.hello()
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                if self.handle(message):
                    break
            except Exception as error:  # noqa: BLE001
                self.system(f"Bridge error: {error}", style="error")
        self.close()

    def handle(self, message: dict) -> bool:
        """Dispatch one client message; True means quit."""
        kind = message.get("type", "")
        if kind == "quit":
            control.request_stop()
            return True
        if kind == "user_message":
            self.on_user_message(
                str(message.get("text", "")),
                image_paths=self._message_image_paths(message.get("images")),
            )
        elif kind == "approval":
            self.resolve_approval(bool(message.get("approved")))
        elif kind == "answer":
            self.answer_question(str(message.get("text", "")))
        elif kind == "yolo":
            self.set_yolo(bool(message.get("value")))
        elif kind == "permission_mode":
            self.set_permission_mode(str(message.get("value", "")))
        elif kind == "pause_toggle":
            self.toggle_pause()
        elif kind == "stop":
            self.stop_turn()
        elif kind == "new_session":
            self.new_session()
        elif kind == "list_sessions":
            self.session_logs = list_session_logs()
            self.send({"type": "sessions", "items": self._session_items()})
        elif kind == "resume_session":
            self.resume_session(message.get("path", ""))
        elif kind == "delete_session":
            self.delete_session(message.get("path", ""))
        elif kind == "rename_session":
            self.rename_session(
                str(message.get("path", "")),
                str(message.get("title", "")),
            )
        elif kind == "fork_session":
            self.fork_session()
        elif kind == "goal":
            self.on_goal(str(message.get("text", "")))
        elif kind == "reviewer":
            self.on_reviewer(str(message.get("text", "")))
        elif kind == "rewind_to_turn":
            self.rewind_to_turn(message.get("turn_id"))
        elif kind == "queue_list":
            self.send({"type": "queue", "items": self.message_queue.items()})
        elif kind == "queue_clear":
            dropped = self.message_queue.clear()
            self.system(f"Cleared {dropped} queued message(s)." if dropped else "No queued messages.")
            self.push_state()
        elif kind == "list_models":
            self.list_models()
        elif kind == "list_skills":
            self.send({"type": "skills", "items": self._skill_items()})
        elif kind == "set_model":
            self.set_model(
                str(message.get("model", "")),
                provider=(str(message["provider"]) if message.get("provider") else None),
            )
        elif kind == "reload_credentials":
            self.reload_credentials()
        return False

    # --- state reporting ------------------------------------------------------

    def push_state(self, *, force_context: bool = False) -> None:
        goal = self.session_goal or load_goal(self.run_log_path)
        self.send(
            {
                "type": "state",
                "state": self.state,
                "workflow": self.workflow_step,
                "turn_active": self.turn_active,
                "yolo": self.always_approve,
                "permission_mode": self.permission_mode,
                "queued": len(self.message_queue),
                "goal_active": bool(goal and goal.status in {STATUS_ACTIVE, STATUS_PAUSED}),
                "session_path": str(self.run_log_path) if self.run_log_path else None,
            }
        )
        self.push_context_line(force=force_context)

    def push_context_line(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_context_line_at < _CONTEXT_LINE_MIN_INTERVAL:
            return
        self._last_context_line_at = now
        try:
            line = self._status_context_line()
        except Exception:  # noqa: BLE001
            return
        totals = load_usage_totals(self.run_log_path)
        cache_available = bool(
            totals
            and int(totals.get("calls_with_cache_data") or 0) > 0
            and totals.get("cache_hit_rate") is not None
        )
        self.send(
            {
                "type": "context_line",
                "text": line,
                "cache_available": cache_available,
                "cache_hit_rate": (
                    float(totals["cache_hit_rate"])
                    if cache_available
                    else 0.0
                ),
            }
        )

    def _status_context_line(self) -> str:
        foreground = current_foreground()
        if foreground is not None:
            return format_status_context_line(
                foreground.messages, foreground.model, label=foreground.label
            )
        if self.main_agent is not None:
            idle_messages = self.main_agent.messages
        elif self.run_log_path:
            idle_messages = build_main_agent_messages(self.run_log_path, "")
        else:
            idle_messages = self.messages
        return format_status_context_line(idle_messages, self.model, label="LangBridge")

    def _on_foreground_change(self) -> None:
        self.push_context_line()

    # --- turns ----------------------------------------------------------------

    @staticmethod
    def _message_image_paths(raw_images) -> list[str]:
        paths = []
        for item in raw_images or []:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, dict) and item.get("path"):
                paths.append(str(item["path"]))
        return paths

    def on_user_message(self, text: str, *, image_paths=None) -> None:
        text = text.strip()
        try:
            images = normalize_image_paths(image_paths)
        except ImageAttachmentError as error:
            self.system(f"Image attachment error: {error}", style="error")
            return
        if not text and not images:
            return
        if self.pending_question is not None:
            if not text:
                return
            self.answer_question(text)
            return
        first_token = text.split(None, 1)[0].lower() if text.startswith("/") else ""
        if first_token == "/reviewer":
            self.on_reviewer(text[len("/reviewer"):].strip())
            return
        from langbridge_code.skills import list_skills, resolve_skill_slash

        status, payload = resolve_skill_slash(text)
        if status == "unknown":
            available = ", ".join(name for name, _ in list_skills(role="langbridge"))
            hint = f" Skills: {available}." if available else ""
            self.system(
                f"Unknown command or skill: /{payload}. Try /help.{hint}",
                style="warn",
            )
            return
        if self.turn_active:
            if self.message_queue.full:
                self.system(
                    f"Queue is full ({len(self.message_queue)} messages). Wait or use /queue clear.",
                    style="warn",
                )
                return
            if self.message_queue.enqueue(text, images):
                waiting = len(self.message_queue)
                label = "message" if waiting == 1 else "messages"
                self.send(
                    {
                        "type": "queued",
                        "text": text,
                        "images": images,
                        "count": waiting,
                    }
                )
                self.system(f"Queued ({waiting} {label} waiting).")
                self.push_state()
            return
        if images:
            self.begin_turn(text, image_paths=images)
        else:
            self.begin_turn(text)

    def begin_turn(self, text: str, *, image_paths=None, announce: bool = False) -> None:
        images = list(image_paths or [])
        control.clear_stop()
        control.resume()
        self.turn_active = True
        self.state = "thinking"
        created_session = self.run_log_path is None
        self.run_log_path = ensure_run_log_path(self.run_log_path, text or "Image")
        self.turn_id += 1
        turn_id = self.turn_id
        try:
            create_checkpoint(self.run_log_path, turn_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            available = checkpoint_available(self.run_log_path, turn_id)
        except Exception:  # noqa: BLE001
            available = False
        from langbridge_code.util.session_traces import append_user_message

        append_user_message(
            self.run_log_path,
            turn_id,
            text,
            image_paths=images,
        )
        if announce:
            self.send(
                {
                    "type": "turn_started",
                    "text": text,
                    "images": images,
                    "turn_id": turn_id,
                    "checkpoint_available": available,
                }
            )
        else:
            self.send(
                {
                    "type": "turn_id_assigned",
                    "turn_id": turn_id,
                    "checkpoint_available": available,
                }
            )
        self.push_state(force_context=True)
        if created_session:
            self.session_logs = list_session_logs()
            self.send({"type": "sessions", "items": self._session_items()})
        threading.Thread(
            target=self.run_turn,
            args=(text,),
            kwargs={"turn_id": turn_id, "image_paths": images},
            daemon=True,
        ).start()

    def run_turn(self, text: str, *, turn_id: int, image_paths=None) -> None:
        trace_id = format_trace_timestamp()
        begin_trace(self.run_log_path, trace_id)
        combined_sink = combine_trace_sink(trace_sink, self._trace_event)

        outcome = ""
        stopped = False
        errored = False
        reply = ""
        reviewer_mode = bool(getattr(self, "_reviewer_mode", False))
        self._reviewer_mode = False
        try:
            session = self._ensure_main_agent(turn_id, text, combined_sink)
            if reviewer_mode:
                reply = session.run_reviewer_loop(
                    text,
                    initial_image_paths=image_paths,
                    on_round=self._on_goal_round,
                    question_callback=self.request_user_answer,
                    on_verdict=self._on_reviewer_verdict,
                )
                outcome = reply or ""
            else:
                goal = self.session_goal or load_goal(self.run_log_path)
                if goal and goal.active:
                    reply, goal = session.run_goal_loop(
                        goal,
                        initial_prompt=text,
                        initial_image_paths=image_paths,
                        on_round=self._on_goal_round,
                        on_verdict=self._on_goal_verdict,
                    )
                    self.session_goal = goal
                    save_goal(self.run_log_path, goal)
                    outcome = reply or ""
                else:
                    reply = session.send(text, image_paths=image_paths)
                    outcome = reply or ""
        except control.StopRequested:
            stopped = True
            outcome = "Stopped by user."
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal and goal.active:
                goal.status = STATUS_PAUSED
                goal.last_reason = "Stopped by user."
                self.session_goal = goal
                save_goal(self.run_log_path, goal)
        except Exception as error:  # noqa: BLE001
            from langbridge_code.llm.client import format_api_error

            errored = True
            outcome = format_api_error(error)
        finally:
            end_trace()
            self._sync_main_messages()
            if stopped:
                self.finish_stopped()
            elif errored:
                self.finish_turn_error(outcome)
            elif reviewer_mode:
                self.finish_turn(reply or "")
            else:
                goal = self.session_goal or load_goal(self.run_log_path)
                if goal and goal.status in {STATUS_ACHIEVED, STATUS_PAUSED}:
                    self.finish_goal_loop(goal, reply or "")
                else:
                    self.finish_turn(reply or "")

    def _ensure_main_agent(self, turn_id, text, combined_sink):
        if self.main_agent is None:
            seed = [{"role": "system", "content": langbridge_system_prompt()}]
            self.main_agent = MainAgentSession(
                self.api_key,
                self.model,
                seed,
                self.run_log_path,
                turn_id,
                target=text,
                trace_sink=combined_sink,
                approval_callback=self.request_approval,
                phase_sink=self._workflow_phase,
                question_callback=self.request_user_answer,
                history_briefing_pending=True,
            )
        else:
            self.main_agent.bind_turn(
                turn_id,
                target=text,
                run_log_path=self.run_log_path,
                trace_sink=combined_sink,
                approval_callback=self.request_approval,
                phase_sink=self._workflow_phase,
                question_callback=self.request_user_answer,
            )
        self.messages = self.main_agent.messages
        return self.main_agent

    def _sync_main_messages(self) -> None:
        if self.main_agent is not None:
            self.messages = self.main_agent.messages

    def finish_turn(self, reply: str) -> None:
        cleaned = strip_bug_status(reply) if reply else ""
        if cleaned:
            self.send({"type": "assistant", "text": cleaned})
        self.send({"type": "turn_end", "status": "ok"})
        self.reset_after_turn(drain_queue=True)

    def finish_stopped(self) -> None:
        self.send({"type": "turn_end", "status": "stopped", "message": "Stopped."})
        self.reset_after_turn()

    def finish_turn_error(self, message: str) -> None:
        self.send({"type": "turn_end", "status": "error", "message": message})
        self.reset_after_turn()

    def finish_goal_loop(self, goal, reply: str) -> None:
        if goal.status == STATUS_ACHIEVED:
            self.system(f"◎ Goal achieved: {goal.last_reason}", style="success")
        elif goal.status == STATUS_PAUSED:
            self.system(f"Goal paused: {goal.last_reason}", style="warn")
        cleaned = strip_bug_status(reply) if reply else ""
        if cleaned:
            self.send({"type": "assistant", "text": cleaned})
        self.send({"type": "turn_end", "status": "ok"})
        self.reset_after_turn(drain_queue=True)

    def reset_after_turn(self, *, drain_queue: bool = False) -> None:
        clear_foreground()
        self.turn_active = False
        self.pending_question = None
        self.pending_approval = None
        self.state = "ready"
        self.workflow_step = ""
        control.clear_stop()
        control.resume()
        if drain_queue:
            queued = self.message_queue.dequeue()
            if queued is not None:
                if isinstance(queued, QueuedUserMessage):
                    self.begin_turn(
                        queued.text,
                        image_paths=queued.image_paths,
                        announce=True,
                    )
                else:
                    self.begin_turn(queued, announce=True)
                return
        self.push_state(force_context=True)

    # --- goal loop callbacks -----------------------------------------------

    def _on_goal_round(self, round_reply: str) -> None:
        cleaned = strip_bug_status(round_reply) if round_reply else ""
        if cleaned:
            self.send({"type": "assistant", "text": cleaned})

    def _on_goal_verdict(self, verdict) -> None:
        if verdict.met:
            return
        line = f"Evaluator: {verdict.reason}"
        if verdict.guidance:
            line += f" — {verdict.guidance}"
        self.system(line)

    def _on_reviewer_verdict(self, verdict) -> None:
        if verdict.note:
            self.system(f"Reviewer: {verdict.note}")

    # --- trace / phase events -----------------------------------------------

    def _trace_event(self, event) -> None:
        kind = getattr(event, "kind", "")
        payload = {
            "type": "stream" if kind.endswith("_stream") else "trace",
            "role": getattr(event, "role", "Agent"),
            "kind": kind,
            "text": getattr(event, "text", ""),
        }
        tool_name = getattr(event, "tool_name", None)
        if tool_name:
            payload["tool"] = tool_name
        self.send(payload)
        if not kind.endswith("_stream"):
            if kind in ("reasoning", "thought"):
                self.state = "thinking"
            elif tool_name == "bash" or kind == "shell":
                self.state = "shell"
            else:
                self.state = "working"
            self.push_state()

    def _workflow_phase(self, phase) -> None:
        self.workflow_step = getattr(phase, "step", str(phase))
        self.push_state(force_context=True)

    # --- approvals / questions ------------------------------------------------

    def request_approval(self, role, tool_name, arguments) -> bool:
        # Bypass preserves the existing circuit breaker and otherwise skips checks.
        if self.permission_mode == "bypass" and circuit_breaker_reason(tool_name, arguments) is None:
            return True
        if self.permission_mode == "auto":
            route, reason = auto_mode_route(tool_name, arguments, workspace=Path.cwd())
            if route == "allow":
                return True
            if route == "block":
                self.system(f"Auto mode blocked {tool_name}: {reason}", style="warn")
                return False
            try:
                decision = AutoModeClassifier(self.api_key, self.model).evaluate(
                    self.messages,
                    role,
                    tool_name,
                    arguments,
                )
            except Exception as error:  # noqa: BLE001 — safe fallback is manual review
                self.system(
                    f"Auto mode classifier unavailable; asking manually ({error}).",
                    style="warn",
                )
            else:
                if decision.allowed:
                    return True
                self.system(
                    f"Auto mode blocked {tool_name} at stage {decision.stage}: {decision.reason}",
                    style="warn",
                )
                return False
        return self._request_manual_approval(role, tool_name, arguments)

    def _request_manual_approval(self, role, tool_name, arguments) -> bool:
        decision = {"approved": False}
        ready = threading.Event()
        self.pending_approval = (decision, ready)
        self.state = "waiting for approval"
        self.send(
            {
                "type": "approval_request",
                "summary": format_approval_request(role, tool_name, arguments),
                "details": format_approval_details(arguments),
            }
        )
        self.push_state()
        ready.wait()
        return decision["approved"]

    def resolve_approval(self, approved: bool) -> None:
        if self.pending_approval is None:
            return
        decision, ready = self.pending_approval
        decision["approved"] = approved
        self.pending_approval = None
        self.send({"type": "approval_resolved", "approved": approved})
        self.state = "working"
        self.push_state()
        ready.set()

    def request_user_answer(self, question, options=None) -> str:
        from langbridge_code.tools.ask_user import format_ask_user_choices

        answer = {"text": ""}
        ready = threading.Event()
        self.pending_question = (answer, ready, list(options or []))
        self.state = "waiting for answer"
        self.send(
            {
                "type": "question",
                "text": format_ask_user_choices(question, options or []),
                "options": list(options or []),
            }
        )
        self.push_state()
        ready.wait()
        return answer["text"]

    def answer_question(self, text: str) -> None:
        from langbridge_code.tools.ask_user import resolve_ask_user_answer

        if self.pending_question is None:
            return
        answer, ready, options = self.pending_question
        answer["text"] = resolve_ask_user_answer(text, options)
        self.pending_question = None
        self.send({"type": "answer_recorded", "text": (text or "").strip() or answer["text"]})
        self.state = "working"
        self.push_state()
        ready.set()

    # --- controls ---------------------------------------------------------------

    def set_yolo(self, value: bool) -> None:
        self.set_permission_mode("bypass" if value else "manual")

    def set_permission_mode(self, value: str) -> None:
        mode = value.strip().lower()
        if mode not in {"manual", "auto", "bypass"}:
            self.system(f"Unknown permission mode: {value}", style="warn")
            return
        self.permission_mode = mode
        self.always_approve = mode == "bypass"
        if mode == "auto":
            self.system("Auto mode on — two-stage safety checks approve actions in the background.", style="accent")
        elif mode == "bypass":
            self.system("Bypass permissions on — tools run without approval.", style="warn")
            if self.pending_approval is not None:
                self.resolve_approval(True)
        else:
            self.system("Manual mode on — high-risk tools need approval.")
        self.push_state()

    def list_models(self) -> None:
        catalog = list_model_catalog(self.api_key)
        if self.model and self.model not in {entry["id"] for entry in catalog}:
            catalog = [
                {"id": self.model, "provider": settings.API_PROVIDER},
                *catalog,
            ]
        self.send(
            {
                "type": "models",
                "items": catalog,
                "current": self.model,
                "provider": settings.API_PROVIDER,
            }
        )

    def set_model(self, model: str, provider: str | None = None) -> None:
        cleaned = (model or "").strip()
        if not cleaned:
            self.system("Model name required. Use /model <id> or pick from the list.", style="warn")
            return
        if self.turn_active:
            self.system("Agent is busy. Use /stop before switching models.", style="warn")
            return
        catalog = list_model_catalog(self.api_key)
        target_provider = provider or infer_provider_for_model(cleaned, catalog=catalog)
        try:
            set_default_model(cleaned, provider=target_provider)
        except ValueError as error:
            self.system(str(error), style="warn")
            return
        if target_provider:
            try:
                self.api_key = load_api_key(target_provider)
            except ValueError as error:
                self.system(str(error), style="error")
                return
        self.model = cleaned
        if self.main_agent is not None:
            self.main_agent.api_key = self.api_key
            self.main_agent.model = cleaned
            self.main_agent._rebuild_subagent_tools()
        self.send(
            {
                "type": "model",
                "model": cleaned,
                "provider": settings.API_PROVIDER,
            }
        )
        label = f"{cleaned} ({settings.API_PROVIDER})" if target_provider else cleaned
        self.system(f"Model set to {label}.")
        self.push_context_line(force=True)

    def reload_credentials(self) -> None:
        """Apply newly saved settings to this bridge without restarting it."""
        try:
            provider, api_key, model = reload_runtime_credentials()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.system(f"Could not reload API settings: {error}", style="error")
            return

        self.api_key = api_key
        self.model = model
        if self.main_agent is not None:
            self.main_agent.api_key = api_key
            self.main_agent.model = model
            self.main_agent._rebuild_subagent_tools()
        self.send(
            {
                "type": "credentials_reloaded",
                "provider": provider,
                "model": model,
            }
        )
        self.push_context_line(force=True)

    def toggle_pause(self) -> None:
        if not self.turn_active:
            return
        if control.is_paused():
            control.resume()
            self.state = "working"
            self.system("Resumed.")
        else:
            control.pause()
            self.state = "paused"
            self.system(
                "Paused. The agent stops at the next step; /pause or Ctrl+P to continue.",
                style="warn",
            )
        self.push_state()

    def stop_turn(self) -> None:
        if not self.turn_active:
            return
        control.request_stop()
        if self.pending_approval is not None:
            self.resolve_approval(False)
        if self.pending_question is not None:
            self.answer_question("")
        self.state = "stopping"
        self.system("Stopping the agent...", style="error")
        self.push_state()

    # --- sessions ------------------------------------------------------------

    def new_session(self) -> None:
        if self.turn_active:
            self.system("Agent is busy. Use /stop first.", style="warn")
            return
        self.run_log_path = None
        self.turn_id = 0
        self.main_agent = None
        self.messages = [{"role": "system", "content": langbridge_system_prompt()}]
        self.session_goal = None
        self.message_queue.clear()
        self.send({"type": "session_new"})
        self.push_state()

    def resume_session(self, path_str: str) -> None:
        if self.turn_active:
            self.system("Agent is busy. Use /stop first.", style="warn")
            return
        path = Path(path_str)
        if not path.exists():
            self.system(f"Session not found: {path_str}", style="warn")
            return
        self.main_agent = None
        self.messages = [{"role": "system", "content": langbridge_system_prompt()}]
        self.run_log_path = path
        self.turn_id = last_turn_id(path)
        self.session_goal = load_goal(path)
        self.turn_active = False
        self.pending_question = None
        self.pending_approval = None
        self.state = "ready"
        self.workflow_step = ""
        self.send(
            {
                "type": "session_resumed",
                "path": str(path),
                "label": label_session(path),
                "preview": self._progress_preview(path),
                "conversation": self._conversation_items(path),
            }
        )
        self.push_state(force_context=True)

    def _conversation_items(self, path) -> list[dict]:
        """Full past user/assistant conversation for the client to replay.

        Each item carries the durable backend ``turn_id`` and whether a
        checkpoint is available for it, so the client can offer rewind on
        eligible historical bubbles.
        """
        from langbridge_code.util.session_traces import read_conversation_items

        items = read_conversation_items(path)
        for item in items:
            turn = item.get("turn_id")
            item["checkpoint_available"] = bool(turn) and checkpoint_available(path, turn)
        return items

    def rewind_to_turn(self, turn_id) -> None:
        """Restore the workspace and context to immediately before ``turn_id``.

        Idle-only, current session only. Truncates that turn and everything
        after it from active history and replaces the client transcript.
        """
        if self.turn_active or self.pending_approval is not None or self.pending_question is not None:
            self.system("Agent is busy. Use /stop before rewinding.", style="warn")
            return
        if self.run_log_path is None:
            self.system("No active session to rewind.", style="warn")
            return
        try:
            target = int(turn_id)
        except (TypeError, ValueError):
            self.system("Invalid rewind target.", style="warn")
            return
        if target <= 0 or target > last_turn_id(self.run_log_path):
            self.system("That message can no longer be restored to.", style="warn")
            return
        if not checkpoint_available(self.run_log_path, target):
            self.system("No checkpoint is available for that message.", style="warn")
            return
        result = restore_checkpoint(self.run_log_path, target)
        if not result.ok:
            self.system(f"Rewind failed: {result.error}", style="error")
            return
        self.main_agent = None
        self.session_goal = None
        self.messages = [{"role": "system", "content": langbridge_system_prompt()}]
        self.turn_id = last_turn_id(self.run_log_path)
        self.state = "ready"
        self.workflow_step = ""
        self.send(
            {
                "type": "rewound",
                "turn_id": target,
                "conversation": self._conversation_items(self.run_log_path),
            }
        )
        self.system("Restored the workspace and conversation to before this message.")
        self.push_state(force_context=True)

    def _progress_preview(self, path) -> str:
        from langbridge_code.util.progress import PROGRESS_HEADER, read_progress

        content = read_progress(path).strip()
        if not content or content == PROGRESS_HEADER.strip():
            return ""
        preview = content.strip()
        if len(preview) > 1200:
            preview = preview[:1200].rstrip() + "\n…"
        return preview

    def delete_session(self, path_str: str) -> None:
        path = Path(path_str)
        session_dir = artifact_dir(path) or path
        try:
            import shutil

            if session_dir.is_dir():
                shutil.rmtree(session_dir)
            elif path.is_file():
                path.unlink()
        except OSError as error:
            self.system(f"Could not delete session: {error}", style="error")
            return
        self.session_logs = [item for item in self.session_logs if str(item) != path_str]
        self.system(f"Deleted session: {session_dir.name}")
        self.send({"type": "sessions", "items": self._session_items()})

    def rename_session(self, path_str: str, title: str) -> None:
        requested_path = Path(path_str) if path_str else self.run_log_path
        if requested_path is None:
            self.system("Start the task before renaming it.", style="warn")
            return
        allowed_paths = {Path(path).resolve() for path in self.session_logs}
        if self.run_log_path is not None:
            allowed_paths.add(Path(self.run_log_path).resolve())
        resolved = Path(requested_path).resolve()
        if resolved not in allowed_paths:
            self.system("Session not found.", style="warn")
            return
        try:
            label = rename_session(resolved, title)
        except (FileNotFoundError, OSError, ValueError) as error:
            self.system(f"Could not rename session: {error}", style="error")
            return
        is_current = self.run_log_path is not None and resolved == Path(self.run_log_path).resolve()
        self.send(
            {
                "type": "session_renamed",
                "path": str(resolved),
                "label": label,
                "current": is_current,
            }
        )
        self.session_logs = list_session_logs()
        self.send({"type": "sessions", "items": self._session_items()})

    def fork_session(self) -> None:
        """Copy the current session into a new, independent session.

        Idle-only, current session only. The fork starts with the same
        durable context snapshot as the source but is otherwise unrelated;
        the source session is left unchanged.
        """
        if self.turn_active or self.pending_approval is not None or self.pending_question is not None:
            self.system("Agent is busy. Use /stop before forking.", style="warn")
            return
        if self.run_log_path is None:
            self.system("Start the task before forking it.", style="warn")
            return
        memory_context = ""
        if self.main_agent is not None:
            memory_context = self.main_agent.context.stack.memory_block or ""
        else:
            has_snapshot, pending_snapshot = read_fork_memory_context(self.run_log_path)
            if has_snapshot:
                memory_context = pending_snapshot
        try:
            forked_path = fork_session(
                self.run_log_path,
                memory_context=memory_context,
            )
        except (FileNotFoundError, OSError) as error:
            self.system(f"Could not fork session: {error}", style="error")
            return
        self.send(
            {
                "type": "session_forked",
                "path": str(forked_path),
                "label": label_session(forked_path),
            }
        )
        self.session_logs = list_session_logs()
        self.send({"type": "sessions", "items": self._session_items()})

    # --- goal -----------------------------------------------------------------

    def on_goal(self, remainder: str) -> None:
        if self.turn_active:
            self.system("Agent is busy. Use /stop first.", style="warn")
            return
        remainder = remainder.strip()
        action = remainder.lower()
        if not remainder or action == "status":
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal is None:
                self.system("No active goal. Use /goal <completion condition>.")
                return
            self.system(format_goal_status(goal))
            return
        if action in ("clear", "cancel"):
            self.session_goal = None
            clear_goal(self.run_log_path)
            self.system("Goal cleared.")
            self.push_state()
            return
        if action == "pause":
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal is None:
                self.system("No active goal to pause.")
                return
            goal.status = STATUS_PAUSED
            self.session_goal = goal
            save_goal(self.run_log_path, goal)
            self.system("Goal paused.", style="warn")
            self.push_state()
            return
        if action == "resume":
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal is None:
                self.system("No goal to resume.")
                return
            if goal.status == STATUS_ACHIEVED:
                self.system("Goal already achieved.", style="success")
                return
            goal.status = STATUS_ACTIVE
            self.session_goal = goal
            save_goal(self.run_log_path, goal)
            self.system(f"Goal resumed: {goal.condition}", style="accent")
            prompt = build_continuation_prompt(goal) if goal.turn_count else goal.condition
            self.begin_turn(prompt, announce=True)
            return
        if not self.run_log_path:
            self.run_log_path = create_run_log_path(remainder)
        condition, _ = parse_goal_command(remainder)
        if not condition:
            self.system("Goal condition cannot be empty.", style="warn")
            return
        self.session_goal = new_goal(remainder)
        save_goal(self.run_log_path, self.session_goal)
        self.system(f"◎ Goal active: {self.session_goal.condition}", style="accent")
        self.push_state()
        self.begin_turn(self.session_goal.condition, announce=True)

    # --- reviewer ---------------------------------------------------------

    def on_reviewer(self, remainder: str) -> None:
        """Post-hoc review: no SessionGoal is created, loaded, or touched."""
        if self.turn_active:
            self.system("Agent is busy. Use /stop first.", style="warn")
            return
        remainder = remainder.strip()
        if not remainder:
            self.system("Usage: /reviewer <request>", style="warn")
            return
        if not self.run_log_path:
            self.run_log_path = create_run_log_path(remainder)
        self._reviewer_mode = True
        self.begin_turn(remainder, announce=True)


def format_approval_request(role, tool_name, arguments):
    path = arguments.get("path")
    if path:
        return f"{role}: approve {tool_name} on {path}?"
    return f"{role}: approve {tool_name}?"


def format_approval_details(arguments):
    if not arguments:
        return ""
    compact = json.dumps(arguments, ensure_ascii=False, indent=2)
    if len(compact) > 600:
        compact = compact[:597] + "..."
    return compact


def run_bridge() -> None:
    try:
        bootstrap_runtime()
    except RuntimeBootstrapError as error:
        print(f"LangBridge runtime setup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    BridgeServer().run()


if __name__ == "__main__":
    run_bridge()
