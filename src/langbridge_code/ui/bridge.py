"""Headless JSONL bridge: the agent engine behind the TypeScript TUI.

Protocol: one JSON object per line.
  stdin  (client -> engine): user_message, approval, answer, yolo, pause_toggle,
          stop, new_session, list_sessions, resume_session, delete_session,
          goal, queue_list, queue_clear, quit
  stdout (engine -> client): hello, system, assistant, turn_started, trace,
          stream, state, context_line, approval_request, question, turn_end,
          sessions, session_resumed, queue

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
from langbridge_code.agents.system_prompt import langbridge_system_prompt
from langbridge_code.context.common.budget import format_status_context_line
from langbridge_code.context.foreground import (
    clear_foreground,
    current_foreground,
    register_foreground_listener,
    unregister_foreground_listener,
)
from langbridge_code.settings import load_api_key
from langbridge_code.tools.approval import circuit_breaker_reason
from langbridge_code.tools.common.runtime import RuntimeBootstrapError, bootstrap_runtime
from langbridge_code.ui.message_queue import UserMessageQueue
from langbridge_code.util.artifacts import artifact_dir, format_trace_timestamp
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
from langbridge_code.util.progress import build_main_agent_messages, finalize_main_agent_turn
from langbridge_code.util.session import (
    create_run_log_path,
    ensure_run_log_path,
    label_session,
    last_turn_id,
    list_session_logs,
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
            return version("langbridge-code")
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
        self.always_approve = False
        self.turn_active = False
        self.state = "ready"
        self.workflow_step = ""
        self.session_goal = None
        self.message_queue = UserMessageQueue()
        self._last_context_line_at = 0.0
        register_foreground_listener(self._on_foreground_change)

    def close(self) -> None:
        unregister_foreground_listener(self._on_foreground_change)
        control.clear_stop()
        control.resume()

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
            }
        )
        self.push_state()

    def _session_items(self) -> list[dict]:
        return [{"path": str(path), "label": label_session(path)} for path in self.session_logs]

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
            self.on_user_message(str(message.get("text", "")))
        elif kind == "approval":
            self.resolve_approval(bool(message.get("approved")))
        elif kind == "answer":
            self.answer_question(str(message.get("text", "")))
        elif kind == "yolo":
            self.set_yolo(bool(message.get("value")))
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
        elif kind == "goal":
            self.on_goal(str(message.get("text", "")))
        elif kind == "queue_list":
            self.send({"type": "queue", "items": self.message_queue.items()})
        elif kind == "queue_clear":
            dropped = self.message_queue.clear()
            self.system(f"Cleared {dropped} queued message(s)." if dropped else "No queued messages.")
            self.push_state()
        return False

    # --- state reporting ------------------------------------------------------

    def push_state(self) -> None:
        goal = self.session_goal or load_goal(self.run_log_path)
        self.send(
            {
                "type": "state",
                "state": self.state,
                "workflow": self.workflow_step,
                "turn_active": self.turn_active,
                "yolo": self.always_approve,
                "queued": len(self.message_queue),
                "goal_active": bool(goal and goal.status in {STATUS_ACTIVE, STATUS_PAUSED}),
            }
        )
        self.push_context_line()

    def push_context_line(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_context_line_at < _CONTEXT_LINE_MIN_INTERVAL:
            return
        self._last_context_line_at = now
        try:
            line = self._status_context_line()
        except Exception:  # noqa: BLE001
            return
        self.send({"type": "context_line", "text": line})

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

    def on_user_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.pending_question is not None:
            self.answer_question(text)
            return
        from langbridge_code.skills import list_skills, resolve_skill_slash

        status, payload = resolve_skill_slash(text)
        if status == "unknown":
            available = ", ".join(name for name, _ in list_skills())
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
            if self.message_queue.enqueue(text):
                waiting = len(self.message_queue)
                label = "message" if waiting == 1 else "messages"
                self.send({"type": "queued", "text": text, "count": waiting})
                self.system(f"Queued ({waiting} {label} waiting).")
                self.push_state()
            return
        self.begin_turn(text)

    def begin_turn(self, text: str, *, announce: bool = False) -> None:
        control.clear_stop()
        control.resume()
        self.turn_active = True
        self.state = "thinking"
        self.run_log_path = ensure_run_log_path(self.run_log_path, text)
        self.turn_id += 1
        turn_id = self.turn_id
        if announce:
            self.send({"type": "turn_started", "text": text})
        self.push_state()
        threading.Thread(
            target=self.run_turn, args=(text,), kwargs={"turn_id": turn_id}, daemon=True
        ).start()

    def run_turn(self, text: str, *, turn_id: int) -> None:
        trace_id = format_trace_timestamp()
        begin_trace(self.run_log_path, trace_id)
        combined_sink = combine_trace_sink(trace_sink, self._trace_event)

        outcome = ""
        stopped = False
        errored = False
        reply = ""
        try:
            session = self._ensure_main_agent(turn_id, text, combined_sink)
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal and goal.active:
                reply, goal = session.run_goal_loop(
                    goal,
                    initial_prompt=text,
                    on_round=self._on_goal_round,
                    on_verdict=self._on_goal_verdict,
                )
                self.session_goal = goal
                save_goal(self.run_log_path, goal)
                outcome = reply or ""
            else:
                reply = session.run_turn(text)
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
            try:
                finalize_main_agent_turn(
                    self.api_key,
                    self.model,
                    self.run_log_path,
                    turn_id,
                    user=text,
                    assistant=outcome,
                )
            except Exception:  # noqa: BLE001
                pass
            if stopped:
                self.finish_stopped()
            elif errored:
                self.finish_turn_error(outcome)
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
                self.begin_turn(queued, announce=True)
                return
        self.push_state()

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
        self.push_state()

    # --- approvals / questions ------------------------------------------------

    def request_approval(self, role, tool_name, arguments) -> bool:
        # Yolo auto-approves everything except root/home removals (circuit breaker).
        if self.always_approve and circuit_breaker_reason(tool_name, arguments) is None:
            return True
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
        self.always_approve = value
        if value:
            self.system("Yolo mode on — write tools auto-approved.", style="warn")
            if self.pending_approval is not None:
                self.resolve_approval(True)
        else:
            self.system("Yolo mode off — write tools need approval again.")
        self.push_state()

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
                "label": label_session(path),
                "preview": self._progress_preview(path),
                "conversation": self._conversation_items(path),
            }
        )
        self.push_state()

    def _conversation_items(self, path) -> list[dict]:
        """Full past user/assistant conversation for the client to replay."""
        from langbridge_code.util.session_traces import read_conversation

        return [{"role": role, "text": text} for role, text in read_conversation(path)]

    def _progress_preview(self, path) -> str:
        from langbridge_code.util.progress import PROGRESS_HEADER, read_progress

        content = read_progress(path).strip()
        if not content or content == PROGRESS_HEADER.strip():
            return ""
        sections = []
        current = []
        for line in content.splitlines():
            if line.startswith("## Turn ") and current:
                sections.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append("\n".join(current))
        preview = (sections[-1] if sections else content).strip()
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
