import os
import re
import subprocess
import threading
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, RichLog, Static, TextArea

from langbridge_code.agents.common import control
from langbridge_code.agents.main_agent import MainAgentSession
from langbridge_code.settings import (
    COMPACT_LOOP_FRACTION,
    DEFAULT_MODEL,
    MAX_AGENT_CONTEXT_TOKENS,
    load_api_key,
)
from langbridge_code.persistence.context import (
    compact_messages_if_needed,
    estimate_tokens,
    restore_full_session_messages,
    restore_session_messages,
)
from langbridge_code.agents.roles import langbridge_system_prompt
from langbridge_code.persistence.goal import (
    STATUS_ACTIVE,
    STATUS_ACHIEVED,
    STATUS_PAUSED,
    build_continuation_prompt,
    clear_goal,
    format_goal_status,
    load_goal,
    new_goal,
    parse_goal_command,
    save_goal,
)
from langbridge_code.persistence.session import (
    create_run_log_path,
    label_session,
    last_turn_id,
    list_session_logs,
    read_session_records,
    write_session_summary,
)
from langbridge_code.ui.message_queue import UserMessageQueue

THEME = "tokyo-night"
ACCENT = "#7aa2f7"
GREEN = "#9ece6a"
YELLOW = "#e0af68"
RED = "#f7768e"


_BUG_STATUS_RE = re.compile(r"\s*BUG_STATUS:\s*[A-Za-z]+\s*$", re.IGNORECASE)


def strip_bug_status(text):
    """Drop a trailing BUG_STATUS control token before showing the reply."""
    return _BUG_STATUS_RE.sub("", text.rstrip()).rstrip()

try:
    from importlib.metadata import PackageNotFoundError, version

    try:
        VERSION = version("langbridge-code")
    except PackageNotFoundError:
        VERSION = "0.1.0"
except Exception:  # noqa: BLE001
    VERSION = "0.1.0"

HELP_TEXT = """Commands:
  /help              show this help
  /new               start a new session
  /sessions          open the session picker (Ctrl+R)
  /resume [n]        open the picker, or resume session number <n>
  /delete <n>        delete session number <n>
  /approve [on|off]  approve a pending action, or toggle auto-approve (yolo)
  /yolo [on|off]     toggle yolo mode (auto-approve all write tools)
  /deny              deny a pending action
  /pause             pause / resume the running agent
  /stop              stop the current turn
  /queue             show queued messages waiting to run
  /queue clear       drop all queued messages
  /goal <condition>  work autonomously until the condition is met
  /goal              show active goal status
  /goal clear        remove the current goal
  /goal pause        pause goal auto-continue
  /goal resume       resume a paused goal
  /banner [on|off]   show or hide the header box (Ctrl+B toggles)
  /exit              quit

Keys: Enter send · Shift+Enter newline · Ctrl+A approve · Ctrl+D deny
      Ctrl+Y yolo · Ctrl+P pause · Ctrl+S stop · Ctrl+R sessions · Ctrl+B header
      Ctrl+C quit · click log + wheel/arrow keys to scroll history
While the agent is busy, Enter queues your message for the next turn."""


class ChatInput(TextArea):
    """Multi-line input where Enter sends and Shift+Enter inserts a newline.

    Pasting keeps every line (TextArea handles paste as a single insert), which
    fixes multi-line task prompts getting truncated to the first line.
    """

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.app.submit_current_input()
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if event.key == "ctrl+y":
            event.stop()
            event.prevent_default()
            self.app.action_toggle_yolo()
            return
        await super()._on_key(event)


class ChatLog(RichLog):
    """Session log that only follows the tail when the user is already at the bottom."""

    can_focus = True

    def append(self, content, **kwargs):
        """Write without pulling the view down if the user scrolled up."""
        self.write(content, scroll_end=self.is_vertical_scroll_end, **kwargs)


class SessionPicker(ModalScreen):
    """A clean, scrollable popup for choosing a saved session to resume.

    Dismisses with the chosen session path, or None when cancelled. The
    OptionList scrolls on its own once there are more sessions than fit.
    """

    CSS = """
    SessionPicker {
        align: center middle;
        background: $background 55%;
    }

    #picker_box {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #picker_title {
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }

    #picker_list {
        height: auto;
        max-height: 16;
        border: none;
        background: $surface;
    }

    #picker_hint {
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, sessions):
        super().__init__()
        self.sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="picker_box"):
            yield Static(f"Resume a session  ({len(self.sessions)})", id="picker_title")
            yield OptionList(*[label_session(path) for path in self.sessions], id="picker_list")
            yield Static("\u2191/\u2193 move \u00b7 Enter resume \u00b7 Esc cancel", id="picker_hint")

    def on_mount(self) -> None:
        self.query_one("#picker_list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.sessions[event.option_index])

    def action_cancel(self) -> None:
        self.dismiss(None)


class LangBridgeTui(App):
    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    #banner {
        height: auto;
        margin: 1 2 0 2;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }

    #banner.hidden {
        display: none;
    }

    #chat_log {
        height: 1fr;
        padding: 1 2;
        background: $background;
        scrollbar-size-vertical: 2;
    }

    #chat_log:focus {
        outline: none;
    }

    #thinking {
        height: auto;
        margin: 0 2;
        padding: 0 1;
        display: none;
    }

    #input_row {
        height: auto;
        margin: 0 2;
        border: round $primary;
        background: $surface;
    }

    #input_row:focus-within {
        border: round $accent;
    }

    #prompt_gutter {
        width: 2;
        height: 100%;
        color: $accent;
        text-style: bold;
        padding: 0 0 0 1;
    }

    #input {
        height: 3;
        max-height: 12;
        width: 1fr;
        border: none;
        background: $surface;
    }

    #status_bar {
        height: 1;
        margin: 0 2 1 2;
        padding: 0 1;
    }

    #status_left {
        width: 1fr;
        color: $text-muted;
    }

    #status_right {
        width: auto;
        text-align: right;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("ctrl+a", "approve_pending", "Approve"),
        ("ctrl+d", "deny_pending", "Deny"),
        ("ctrl+y", "toggle_yolo", "Yolo"),
        ("ctrl+p", "toggle_pause", "Pause"),
        ("ctrl+s", "stop", "Stop"),
        ("ctrl+r", "open_sessions", "Sessions"),
        ("ctrl+b", "toggle_banner", "Header"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, api_key=None, model=None):
        super().__init__()
        self.api_key = api_key or load_api_key()
        self.model = model or os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
        self.session_logs = list_session_logs()
        self.run_log_path = None
        self.turn_id = 0
        self.messages = [{"role": "system", "content": langbridge_system_prompt()}]
        self.pending_approval = None
        self.pending_question = None
        self.always_approve = False
        self.turn_active = False
        self.turn_snapshot = None
        self.state = "ready"
        self.streaming_phase = "idle"
        self.workflow_step = ""
        self.session_goal = None
        self.message_queue = UserMessageQueue()
        self.banner_visible = True
        self.cwd_display = self._short_cwd()
        self.git_branch = self._git_branch()

    def compose(self) -> ComposeResult:
        yield Static(id="banner")
        yield ChatLog(id="chat_log", wrap=True, markup=False, auto_scroll=False)
        yield Static("", id="thinking")
        with Horizontal(id="input_row"):
            yield Static("\u276f", id="prompt_gutter")
            yield ChatInput(id="input")
        with Horizontal(id="status_bar"):
            yield Static("", id="status_left")
            yield Static("", id="status_right")

    def on_mount(self) -> None:
        self.title = "LangBridge Code"
        self.theme = THEME
        self.query_one("#banner", Static).border_title = "LangBridge Code"
        self.session_logs = list_session_logs()
        if self.session_logs:
            self.push_screen(SessionPicker(self.session_logs), self._on_startup_session_choice)
        else:
            self.start_new_session()
            self.update_status()
            self.query_one("#input", ChatInput).focus()

    def _on_startup_session_choice(self, path) -> None:
        if path is not None:
            self.resume_session(path)
        else:
            self.start_new_session()
        self.update_status()
        self.query_one("#input", ChatInput).focus()

    # --- conversation rendering -------------------------------------------

    def _log(self) -> ChatLog:
        return self.query_one("#chat_log", ChatLog)

    def write_user(self, text, *, queued=False):
        line = Text()
        line.append("\u2726 ", style=f"bold {ACCENT}")
        line.append(text)
        if queued:
            line.append(" (queued)", style="dim")
        self._log().append(line)

    def write_assistant(self, text):
        line = Text()
        line.append("\u25cf ", style=f"bold {GREEN}")
        line.append(text)
        self._log().append(line)

    def _thinking(self) -> Static:
        return self.query_one("#thinking", Static)

    def set_thinking(self, role, text):
        flat = " ".join(str(text).split())
        if len(flat) > 200:
            flat = flat[:197] + "..."
        line = Text()
        line.append("\u2026 ", style=f"dim {ACCENT}")
        line.append(f"{role} thinking", style=f"italic {ACCENT}")
        line.append(f": {flat}", style="dim italic")
        widget = self._thinking()
        widget.update(line)
        widget.display = True

    def clear_thinking(self):
        widget = self._thinking()
        widget.update("")
        widget.display = False

    def write_system(self, text, style="dim"):
        self._log().append(Text(text, style=style))

    def add_chat_line(self, line):
        self.write_system(line)

    def _input_box(self) -> ChatInput:
        return self.query_one("#input", ChatInput)

    def _ensure_input_writable(self, *, focus: bool = False) -> None:
        """Keep the composer usable while a turn runs (queue follow-ups)."""
        input_box = self._input_box()
        input_box.disabled = False
        if focus:
            input_box.focus()

    # --- input / commands -------------------------------------------------

    def submit_current_input(self) -> None:
        input_box = self._input_box()
        text = input_box.text.strip()
        if not text:
            return
        input_box.text = ""

        if text.startswith("/"):
            self.handle_command(text)
            return
        if self.pending_question is not None:
            self.answer_question(text)
            return
        if self.turn_active:
            self.enqueue_user_message(text)
            return

        self.write_user(text)
        self.begin_turn(text)

    def enqueue_user_message(self, text: str) -> None:
        if not self.message_queue.enqueue(text):
            if self.message_queue.full:
                self.write_system(
                    f"Queue is full ({len(self.message_queue)} messages). "
                    "Wait or use /queue clear.",
                    style=YELLOW,
                )
            return
        self.write_user(text, queued=True)
        waiting = len(self.message_queue)
        label = "message" if waiting == 1 else "messages"
        self.write_system(f"Queued ({waiting} {label} waiting).", style="dim")
        self._ensure_input_writable(focus=True)
        self.update_status()

    def drain_message_queue(self) -> bool:
        text = self.message_queue.dequeue()
        if not text:
            return False
        self.begin_turn(text, show_user=False)
        return True

    def begin_turn(self, text, *, show_user=False):
        if show_user:
            self.write_user(text)
        control.clear_stop()
        control.resume()
        self.turn_active = True
        self.state = "thinking"
        self._sync_streaming_phase()
        self.turn_snapshot = list(self.messages)
        self._ensure_input_writable(focus=True)
        self.update_status()
        self.run_worker(lambda: self.run_turn(text), thread=True)

    def handle_command(self, text):
        parts = text.split()
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/help":
            self.write_system(HELP_TEXT)
        elif cmd == "/new":
            self.cmd_new()
        elif cmd == "/sessions":
            self.cmd_sessions()
        elif cmd == "/resume":
            self.cmd_resume(arg)
        elif cmd == "/delete":
            self.cmd_delete(arg)
        elif cmd == "/approve":
            if arg in ("on", "off"):
                self.set_yolo_mode(arg == "on")
            else:
                self.action_approve_pending()
        elif cmd == "/yolo":
            if arg in ("on", "off"):
                self.set_yolo_mode(arg == "on")
            else:
                self.action_toggle_yolo()
        elif cmd == "/deny":
            self.action_deny_pending()
        elif cmd == "/pause":
            self.action_toggle_pause()
        elif cmd == "/stop":
            self.action_stop()
        elif cmd == "/queue":
            self.cmd_queue(arg)
        elif cmd == "/goal":
            self.cmd_goal(text)
        elif cmd == "/banner":
            if arg == "on":
                self.set_banner_visible(True)
            elif arg == "off":
                self.set_banner_visible(False)
            else:
                self.action_toggle_banner()
        else:
            self.write_system(f"Unknown command: {cmd}. Try /help.", style=YELLOW)

    def cmd_queue(self, arg):
        if arg == "clear":
            dropped = self.message_queue.clear()
            if dropped:
                self.write_system(f"Cleared {dropped} queued message(s).", style="dim")
            else:
                self.write_system("No queued messages.", style="dim")
            self.update_status()
            return
        items = self.message_queue.items()
        if not items:
            self.write_system("No queued messages.", style="dim")
            return
        self.write_system("Queued messages (next first):", style="dim")
        for index, item in enumerate(items, start=1):
            preview = item.replace("\n", " ")
            if len(preview) > 120:
                preview = preview[:117] + "..."
            self.write_system(f"  {index}. {preview}", style="dim")

    def cmd_goal(self, text):
        if self.turn_active:
            self.write_system("Agent is busy. Use /stop first.", style=YELLOW)
            return
        remainder = text[len("/goal") :].strip()
        action = remainder.lower()
        if not remainder:
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal is None:
                self.write_system("No active goal. Use /goal <completion condition>.", style="dim")
                return
            self.write_system(format_goal_status(goal), style="dim")
            return
        if action in ("clear", "cancel"):
            self.session_goal = None
            clear_goal(self.run_log_path)
            self.write_system("Goal cleared.", style="dim")
            self.update_status()
            return
        if action == "pause":
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal is None:
                self.write_system("No active goal to pause.", style="dim")
                return
            goal.status = STATUS_PAUSED
            self.session_goal = goal
            save_goal(self.run_log_path, goal)
            self.write_system("Goal paused.", style=YELLOW)
            self.update_status()
            return
        if action == "resume":
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal is None:
                self.write_system("No goal to resume.", style="dim")
                return
            if goal.status == STATUS_ACHIEVED:
                self.write_system("Goal already achieved.", style=GREEN)
                return
            goal.status = STATUS_ACTIVE
            self.session_goal = goal
            save_goal(self.run_log_path, goal)
            self.write_system(f"Goal resumed: {goal.condition}", style=ACCENT)
            prompt = build_continuation_prompt(goal) if goal.turn_count else goal.condition
            self.begin_turn(prompt)
            return
        if action == "status":
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal is None:
                self.write_system("No active goal.", style="dim")
                return
            self.write_system(format_goal_status(goal), style="dim")
            return
        if not self.run_log_path:
            self.start_new_session()
        condition, _ = parse_goal_command(remainder)
        if not condition:
            self.write_system("Goal condition cannot be empty.", style=YELLOW)
            return
        self.session_goal = new_goal(remainder)
        save_goal(self.run_log_path, self.session_goal)
        self.write_system(f"◎ Goal active: {self.session_goal.condition}", style=ACCENT)
        self.update_status()
        self.write_user(self.session_goal.condition)
        self.begin_turn(self.session_goal.condition)

    def run_turn(self, text):
        self.turn_id += 1
        threshold = int(MAX_AGENT_CONTEXT_TOKENS * COMPACT_LOOP_FRACTION)
        if estimate_tokens(self.messages) > threshold:
            fresh = restore_full_session_messages(read_session_records(self.run_log_path))
            result = compact_messages_if_needed(
                fresh,
                api_key=self.api_key,
                model=self.model,
                label="Workflow session compaction",
            )
            self.messages = fresh
            if result["compacted"]:
                self.call_from_thread(self.add_chat_line, "(compacted older context to stay under the token budget)")

        try:
            session = MainAgentSession(
                self.api_key,
                self.model,
                self.messages,
                self.run_log_path,
                self.turn_id,
                target=text,
                trace_sink=self.post_trace_event,
                approval_callback=self.request_approval,
                phase_sink=self.post_workflow_phase,
                question_callback=self.request_user_answer,
            )
            goal = self.session_goal or load_goal(self.run_log_path)
            if goal and goal.active:
                reply, goal = session.run_goal_loop(
                    goal,
                    initial_prompt=text,
                    on_round=lambda round_reply: self.call_from_thread(
                        self._on_goal_round, round_reply
                    ),
                    on_verdict=lambda verdict: self.call_from_thread(
                        self._on_goal_verdict, verdict
                    ),
                )
                self.session_goal = goal
                save_goal(self.run_log_path, goal)
            else:
                reply = session.run_turn(text)
        except control.StopRequested:
            self.call_from_thread(self.finish_stopped)
            return
        except Exception as error:
            from langbridge_code.llm.client import format_api_error

            self.call_from_thread(self.finish_turn_error, format_api_error(error))
            return
        write_session_summary(self.api_key, self.model, self.run_log_path)
        goal = self.session_goal or load_goal(self.run_log_path)
        if goal and goal.status in {STATUS_ACHIEVED, STATUS_PAUSED}:
            self.call_from_thread(self.finish_goal_loop, goal, reply or "")
        else:
            self.call_from_thread(self.finish_turn, reply or "")

    def _on_goal_round(self, reply):
        cleaned = strip_bug_status(reply) if reply else ""
        if cleaned:
            self.write_assistant(cleaned)

    def _on_goal_verdict(self, verdict):
        if verdict.met:
            return
        line = f"Evaluator: {verdict.reason}"
        if verdict.guidance:
            line += f" — {verdict.guidance}"
        self.write_system(line, style="dim")

    def finish_goal_loop(self, goal, reply):
        if goal.status == STATUS_ACHIEVED:
            self.write_system(f"◎ Goal achieved: {goal.last_reason}", style=GREEN)
        elif goal.status == STATUS_PAUSED:
            self.write_system(f"Goal paused: {goal.last_reason}", style=YELLOW)
        self.reset_after_turn()

    def post_trace_event(self, event):
        self.call_from_thread(self.add_trace_event, event)

    def _sync_streaming_phase(self):
        step_map = {
            "routing": "thinking",
            "planning": "composing",
            "coding": "composing",
            "reviewing": "thinking",
            "presenting": "composing",
            "refining": "composing",
            "summarizing": "composing",
            "evaluating": "thinking",
        }
        if self.workflow_step in step_map:
            self.streaming_phase = step_map[self.workflow_step]
            return
        mapping = {
            "ready": "idle",
            "thinking": "thinking",
            "working": "composing",
            "waiting for approval": "waiting",
            "waiting for answer": "waiting",
            "paused": "waiting",
            "stopping": "shell",
        }
        self.streaming_phase = mapping.get(self.state, "composing")

    def set_workflow_phase(self, phase):
        self.workflow_step = getattr(phase, "step", str(phase))
        self._sync_streaming_phase()
        self.update_status()

    def post_workflow_phase(self, phase):
        self.call_from_thread(self.set_workflow_phase, phase)

    def append_trace_line(self, event):
        line = Text()
        role = getattr(event, "role", "Agent")
        text = getattr(event, "text", "")
        if event.kind == "action":
            line.append(f"{role}: ", style=f"dim italic {ACCENT}")
            line.append(f"\u21b3 {text}", style="dim italic")
        else:
            line.append(f"{role}: ", style=f"dim italic {ACCENT}")
            line.append(text, style="dim italic")
        self._log().append(line)

    def add_trace_event(self, event):
        self.append_trace_line(event)
        if event.kind in ("reasoning", "thought"):
            self.set_thinking(event.role, event.text)
            self.state = "thinking"
        elif getattr(event, "tool_name", None) == "bash" or event.kind == "shell":
            self.state = "working"
            self.streaming_phase = "shell"
        else:
            self.state = "working"
            self.streaming_phase = "composing"
        self.update_status()

    # --- approvals --------------------------------------------------------

    def request_approval(self, role, tool_name, arguments):
        if self.always_approve:
            return True
        decision = {"approved": False}
        ready = threading.Event()
        shown = threading.Event()
        self.call_from_thread(self.show_approval, role, tool_name, arguments, decision, ready, shown)
        shown.wait()
        ready.wait()
        return decision["approved"]

    def show_approval(self, role, tool_name, arguments, decision, ready, shown):
        self.pending_approval = (decision, ready)
        summary = format_approval_request(role, tool_name, arguments)
        details = format_approval_details(arguments)
        self.write_system(f"\u26a0 Approval needed: {summary}", style=f"bold {YELLOW}")
        if details:
            self.write_system(details, style="dim")
        self.write_system("Ctrl+A approve \u00b7 Ctrl+D deny \u00b7 Ctrl+Y yolo  (or /approve, /deny, /yolo)", style="dim")
        self.state = "waiting for approval"
        self._sync_streaming_phase()
        self.update_status()
        shown.set()

    def resolve_approval(self, approved):
        if self.pending_approval is None:
            return
        decision, ready = self.pending_approval
        decision["approved"] = approved
        self.pending_approval = None
        self.write_system("\u2713 Approved." if approved else "\u2717 Denied.", style=GREEN if approved else RED)
        self.state = "working"
        self._sync_streaming_phase()
        self.update_status()
        ready.set()

    # --- planner questions ------------------------------------------------

    def request_user_answer(self, question, options=None):
        """Called from the worker thread when the planner asks the user."""
        answer = {"text": ""}
        ready = threading.Event()
        shown = threading.Event()
        self.call_from_thread(self.show_question, question, options or [], answer, ready, shown)
        shown.wait()
        ready.wait()
        return answer["text"]

    def show_question(self, question, options, answer, ready, shown):
        from langbridge_code.tools.ask_user import format_ask_user_choices

        self.pending_question = (answer, ready, list(options))
        self.write_system("\u2753 Planner asks:", style=f"bold {ACCENT}")
        self.write_system(format_ask_user_choices(question, options), style=ACCENT)
        self.state = "waiting for answer"
        self._sync_streaming_phase()
        self._ensure_input_writable(focus=True)
        self.update_status()
        shown.set()

    def answer_question(self, text):
        from langbridge_code.tools.ask_user import resolve_ask_user_answer

        if self.pending_question is None:
            return
        answer, ready, options = self.pending_question
        answer["text"] = resolve_ask_user_answer(text, options)
        self.pending_question = None
        display = (text or "").strip() or answer["text"]
        if display:
            self.write_user(display)
        self.state = "working"
        self._sync_streaming_phase()
        self.update_status()
        ready.set()

    def action_approve_pending(self) -> None:
        if self.pending_approval is not None:
            self.resolve_approval(True)

    def action_deny_pending(self) -> None:
        if self.pending_approval is not None:
            self.resolve_approval(False)

    def action_toggle_yolo(self) -> None:
        self.set_yolo_mode(not self.always_approve)

    def set_yolo_mode(self, value):
        self.always_approve = value
        if value:
            self.write_system(
                "Yolo mode on — write tools auto-approved.",
                style=f"bold {YELLOW}",
            )
            if self.pending_approval is not None:
                self.resolve_approval(True)
        else:
            self.write_system("Yolo mode off — write tools need approval again.", style="dim")
        self.update_status()

    def set_always_approve(self, value):
        self.set_yolo_mode(value)

    # --- pause / stop -----------------------------------------------------

    def action_toggle_pause(self) -> None:
        if not self.turn_active:
            return
        if control.is_paused():
            control.resume()
            self.state = "working"
            self._sync_streaming_phase()
            self.write_system("Resumed.", style="dim")
        else:
            control.pause()
            self.state = "paused"
            self._sync_streaming_phase()
            self.write_system("Paused. The agent stops at the next step; /pause or Ctrl+P to continue.", style=YELLOW)
        self.update_status()

    def action_stop(self) -> None:
        if not self.turn_active:
            return
        control.request_stop()
        if self.pending_approval is not None:
            self.resolve_approval(False)
        if self.pending_question is not None:
            self.answer_question("")
        self.state = "stopping"
        self._sync_streaming_phase()
        self.update_status()
        self.write_system("Stopping the agent...", style=RED)

    # --- turn lifecycle ---------------------------------------------------

    def finish_turn(self, reply):
        cleaned = strip_bug_status(reply) if reply else ""
        if cleaned:
            self.write_assistant(cleaned)
        self.reset_after_turn()

    def finish_stopped(self):
        if self.turn_snapshot is not None:
            self.messages = self.turn_snapshot
        goal = self.session_goal or load_goal(self.run_log_path)
        if goal and goal.active:
            goal.status = STATUS_PAUSED
            goal.last_reason = "Stopped by user."
            self.session_goal = goal
            save_goal(self.run_log_path, goal)
        self.write_system("\u25a0 Stopped.", style=RED)
        self.reset_after_turn()

    def finish_turn_error(self, message):
        if self.turn_snapshot is not None:
            self.messages = self.turn_snapshot
        self.write_system(f"\u25a0 {message}", style=RED)
        self.reset_after_turn()

    def reset_after_turn(self):
        self.clear_thinking()
        self.turn_active = False
        self.turn_snapshot = None
        self.pending_question = None
        self.state = "ready"
        self.workflow_step = ""
        self._sync_streaming_phase()
        control.clear_stop()
        control.resume()
        if self.drain_message_queue():
            return
        self._ensure_input_writable(focus=True)
        self.update_status()

    # --- sessions ---------------------------------------------------------

    def cmd_new(self):
        if self.turn_active:
            self.write_system("Agent is busy. Use /stop first.", style=YELLOW)
            return
        self._log().clear()
        self.start_new_session()
        self.update_status()

    def action_open_sessions(self) -> None:
        self.open_session_picker()

    def cmd_sessions(self):
        self.open_session_picker()

    def open_session_picker(self):
        if self.turn_active:
            self.write_system("Agent is busy. Use /stop first.", style=YELLOW)
            return
        self.session_logs = list_session_logs()
        if not self.session_logs:
            self.write_system("No saved sessions.", style="dim")
            return
        self.push_screen(SessionPicker(self.session_logs), self.on_session_picked)

    def on_session_picked(self, path):
        if path is not None:
            self.resume_session(path)

    def cmd_resume(self, arg):
        if arg is None:
            self.open_session_picker()
            return
        path = self._session_at(arg)
        if path is None:
            return
        self.resume_session(path)

    def resume_session(self, path):
        records = read_session_records(path)
        self.messages = restore_session_messages(
            records, api_key=self.api_key, model=self.model
        ) or [{"role": "system", "content": langbridge_system_prompt()}]
        self.run_log_path = path
        self.turn_id = last_turn_id(records)
        self.session_goal = load_goal(path)
        self._log().clear()
        self.write_system(f"Resumed: {label_session(path)}", style="dim")
        self._replay_session_records(records)
        self.update_banner()
        self.update_status()

    def _replay_session_records(self, records):
        for record in records:
            user = record.get("user")
            assistant = record.get("assistant", "")
            steps = record.get("steps") or []
            if user:
                self.write_user(user)
            if assistant:
                cleaned = strip_bug_status(assistant)
                if cleaned:
                    self.write_assistant(cleaned)
            elif steps:
                self.write_system(
                    "\u25a0 Agent was working\u2026 (interrupted mid-turn)",
                    style=YELLOW,
                )
            elif user:
                self.write_system(
                    "\u25a0 No reply yet (turn interrupted before the agent finished)",
                    style=YELLOW,
                )

    def cmd_delete(self, arg):
        path = self._session_at(arg)
        if path is None:
            return
        try:
            path.unlink()
        except OSError as error:
            self.write_system(f"Could not delete session: {error}", style=RED)
            return
        self.session_logs = [item for item in self.session_logs if item != path]
        self.write_system(f"Deleted session: {path.name}", style="dim")

    def _session_at(self, arg):
        self.session_logs = list_session_logs()
        if not arg or not arg.isdigit():
            self.write_system("Usage: /resume <n> (see /sessions).", style=YELLOW)
            return None
        index = int(arg) - 1
        if index < 0 or index >= len(self.session_logs):
            self.write_system(f"No session number {arg}. See /sessions.", style=YELLOW)
            return None
        return self.session_logs[index]

    def start_new_session(self):
        self.run_log_path = create_run_log_path()
        self.turn_id = 0
        self.messages = [{"role": "system", "content": langbridge_system_prompt()}]
        self.session_goal = None
        self.message_queue.clear()
        self.update_banner()

    # --- banner & status --------------------------------------------------

    def action_toggle_banner(self) -> None:
        self.set_banner_visible(not self.banner_visible)

    def set_banner_visible(self, visible: bool) -> None:
        self.banner_visible = visible
        banner = self.query_one("#banner", Static)
        if visible:
            banner.remove_class("hidden")
        else:
            banner.add_class("hidden")
        self.update_status()

    def update_banner(self):
        try:
            session_label = label_session(self.run_log_path)
        except Exception:  # noqa: BLE001
            session_label = self.run_log_path.name if self.run_log_path else "new"
        body = Text()
        body.append("Send /help for commands.\n\n", style="dim")
        for name, value in (
            ("Directory", self.cwd_display),
            ("Session", session_label),
            ("Model", self.model),
            ("Version", VERSION),
        ):
            body.append(f"{name + ':':<11}", style="dim")
            body.append(f"{value}\n")
        self.query_one("#banner", Static).update(body)

    def update_status(self):
        left = Text()
        left.append(self.model, style=ACCENT)
        left.append("  ")
        left.append(self.streaming_phase, style=self._state_style())
        if self.workflow_step:
            left.append(f" · {self.workflow_step}", style="dim")
        goal = self.session_goal or load_goal(self.run_log_path)
        if goal and goal.status in {STATUS_ACTIVE, STATUS_PAUSED}:
            left.append(" · ◎ goal", style=f"bold {ACCENT}")
        if self.always_approve:
            left.append(" · yolo", style=f"bold {YELLOW}")
        queued = len(self.message_queue)
        if queued:
            label = "msg" if queued == 1 else "msgs"
            left.append(f" · {queued} queued {label}", style=f"bold {YELLOW}")
        left.append("   ")
        left.append(self.cwd_display, style="dim")
        if self.git_branch:
            left.append(f"  \u2387 {self.git_branch}", style=f"dim {GREEN}")
        self.query_one("#status_left", Static).update(left)

        used = estimate_tokens(self.messages)
        pct = used / MAX_AGENT_CONTEXT_TOKENS * 100 if MAX_AGENT_CONTEXT_TOKENS else 0
        right = Text()
        right.append(f"context {pct:.1f}% ({_fmt_k(used)}/{_fmt_k(MAX_AGENT_CONTEXT_TOKENS)})", style="dim")
        header_hint = "ctrl+b header" if self.banner_visible else "ctrl+b show header"
        right.append(f"   {header_hint} \u00b7 ctrl+c quit \u00b7 /help", style="dim")
        self.query_one("#status_right", Static).update(right)

    def _state_style(self):
        if self.state == "ready":
            return "dim"
        if self.state == "stopping":
            return RED
        return YELLOW

    def _short_cwd(self):
        home = str(Path.home())
        cwd = str(Path.cwd())
        return "~" + cwd[len(home):] if cwd.startswith(home) else cwd

    def _git_branch(self):
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


def _fmt_k(n):
    return f"{n / 1000:.1f}k"


def format_approval_request(role, tool_name, arguments):
    path = arguments.get("path")
    if path:
        return f"{role}: approve {tool_name} on {path}?"
    return f"{role}: approve {tool_name}?"


def format_approval_details(arguments):
    if not arguments:
        return ""
    import json

    compact = json.dumps(arguments, ensure_ascii=False, indent=2)
    if len(compact) > 600:
        compact = compact[:597] + "..."
    return compact


def run_tui():
    LangBridgeTui().run()
