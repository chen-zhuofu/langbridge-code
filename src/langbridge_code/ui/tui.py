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

from langbridge_code.agents import control
from langbridge_code.workflow.run import run_workflow
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
from langbridge_code.agents.roles import CHAT_SYSTEM_PROMPT as SYSTEM_PROMPT
from langbridge_code.persistence.session import (
    create_run_log_path,
    label_session,
    last_turn_id,
    list_session_logs,
    read_session_records,
    write_session_summary,
)

THEME = "tokyo-night"
ACCENT = "#7aa2f7"
GREEN = "#9ece6a"
YELLOW = "#e0af68"
RED = "#f7768e"


_BUG_STATUS_RE = re.compile(r"\s*BUG_STATUS:\s*[A-Za-z]+\s*$", re.IGNORECASE)


def strip_bug_status(text):
    """Drop the PM's trailing BUG_STATUS control token before showing the reply.

    BUG_STATUS is a loop-control token the PM appends to every round; it drives
    pm_should_continue, not the user, so it should not surface in the chat. The
    PM sometimes puts it on its own line and sometimes inline after the reply,
    so we strip it from the end either way.
    """
    return _BUG_STATUS_RE.sub("", text.rstrip()).rstrip()

try:
    from importlib.metadata import PackageNotFoundError, version

    try:
        VERSION = version("langbridge-code")
    except PackageNotFoundError:
        try:
            VERSION = version("langbridge-cli")
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
  /approve [on|off]  approve a pending action, or toggle auto-approve
  /deny              deny a pending action
  /pause             pause / resume the running agent
  /stop              stop the current turn
  /exit              quit

Keys: Enter send · Shift+Enter newline · Ctrl+A approve · Ctrl+D deny
      Ctrl+P pause · Ctrl+S stop · Ctrl+R sessions · Ctrl+C quit"""


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
        await super()._on_key(event)


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

    #chat_log {
        height: 1fr;
        padding: 1 2;
        background: $background;
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
        ("ctrl+p", "toggle_pause", "Pause"),
        ("ctrl+s", "stop", "Stop"),
        ("ctrl+r", "open_sessions", "Sessions"),
        ("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, api_key=None, model=None):
        super().__init__()
        self.api_key = api_key or load_api_key()
        self.model = model or os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
        self.session_logs = list_session_logs()
        self.run_log_path = None
        self.turn_id = 0
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.pending_approval = None
        self.always_approve = False
        self.turn_active = False
        self.turn_snapshot = None
        self.state = "ready"
        self.streaming_phase = "idle"
        self.workflow_step = ""
        self.cwd_display = self._short_cwd()
        self.git_branch = self._git_branch()

    def compose(self) -> ComposeResult:
        yield Static(id="banner")
        yield RichLog(id="chat_log", wrap=True, markup=False)
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
        self.start_new_session()
        self.update_status()
        self.query_one("#input", ChatInput).focus()

    # --- conversation rendering -------------------------------------------

    def _log(self) -> RichLog:
        return self.query_one("#chat_log", RichLog)

    def write_user(self, text):
        line = Text()
        line.append("\u2726 ", style=f"bold {ACCENT}")
        line.append(text)
        self._log().write(line)

    def write_assistant(self, text):
        line = Text()
        line.append("\u25cf ", style=f"bold {GREEN}")
        line.append(text)
        self._log().write(line)

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
        self._log().write(Text(text, style=style))

    def add_chat_line(self, line):
        self.write_system(line)

    # --- input / commands -------------------------------------------------

    def submit_current_input(self) -> None:
        input_box = self.query_one("#input", ChatInput)
        text = input_box.text.strip()
        if not text:
            return
        input_box.text = ""

        if text.startswith("/"):
            self.handle_command(text)
            return
        if self.turn_active:
            self.write_system("Agent is busy. Use /stop first.", style=YELLOW)
            return

        self.write_user(text)
        input_box.disabled = True
        control.clear_stop()
        control.resume()
        self.turn_active = True
        self.state = "thinking"
        self._sync_streaming_phase()
        self.turn_snapshot = list(self.messages)
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
                self.set_always_approve(arg == "on")
            else:
                self.action_approve_pending()
        elif cmd == "/deny":
            self.action_deny_pending()
        elif cmd == "/pause":
            self.action_toggle_pause()
        elif cmd == "/stop":
            self.action_stop()
        else:
            self.write_system(f"Unknown command: {cmd}. Try /help.", style=YELLOW)

    def run_turn(self, text):
        self.turn_id += 1
        threshold = int(MAX_AGENT_CONTEXT_TOKENS * COMPACT_LOOP_FRACTION)
        if estimate_tokens(self.messages) > threshold:
            fresh = restore_full_session_messages(read_session_records(self.run_log_path))
            result = compact_messages_if_needed(
                fresh,
                api_key=self.api_key,
                model=self.model,
                label="PM session compaction",
            )
            self.messages = fresh
            if result["compacted"]:
                self.call_from_thread(self.add_chat_line, "(compacted older context to stay under the token budget)")

        try:
            reply = run_workflow(
                self.api_key,
                self.model,
                text,
                self.run_log_path,
                self.turn_id,
                trace_sink=self.post_trace_event,
                phase_sink=self.post_workflow_phase,
                print_reply=False,
                approval_callback=self.request_approval,
                messages=self.messages,
            )
        except control.StopRequested:
            self.call_from_thread(self.finish_stopped)
            return
        write_session_summary(self.api_key, self.model, self.run_log_path)
        self.call_from_thread(self.finish_turn, reply or "")

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
        }
        if self.workflow_step in step_map:
            self.streaming_phase = step_map[self.workflow_step]
            return
        mapping = {
            "ready": "idle",
            "thinking": "thinking",
            "working": "composing",
            "waiting for approval": "waiting",
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

    def add_trace_event(self, event):
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
        self.write_system("Ctrl+A approve \u00b7 Ctrl+D deny  (or /approve, /deny)", style="dim")
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

    def action_approve_pending(self) -> None:
        if self.pending_approval is not None:
            self.resolve_approval(True)

    def action_deny_pending(self) -> None:
        if self.pending_approval is not None:
            self.resolve_approval(False)

    def set_always_approve(self, value):
        self.always_approve = value
        self.write_system(f"Auto-approve {'on' if value else 'off'}.", style="dim")

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
        self.write_system("\u25a0 Stopped.", style=RED)
        self.reset_after_turn()

    def reset_after_turn(self):
        self.clear_thinking()
        self.turn_active = False
        self.turn_snapshot = None
        self.state = "ready"
        self.workflow_step = ""
        self._sync_streaming_phase()
        control.clear_stop()
        control.resume()
        input_box = self.query_one("#input", ChatInput)
        input_box.disabled = False
        input_box.focus()
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
        ) or [{"role": "system", "content": SYSTEM_PROMPT}]
        self.run_log_path = path
        self.turn_id = last_turn_id(records)
        self._log().clear()
        self.write_system(f"Resumed: {label_session(path)}", style="dim")
        self.update_banner()
        self.update_status()

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
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.update_banner()

    # --- banner & status --------------------------------------------------

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
        left.append("   ")
        left.append(self.cwd_display, style="dim")
        if self.git_branch:
            left.append(f"  \u2387 {self.git_branch}", style=f"dim {GREEN}")
        self.query_one("#status_left", Static).update(left)

        used = estimate_tokens(self.messages)
        pct = used / MAX_AGENT_CONTEXT_TOKENS * 100 if MAX_AGENT_CONTEXT_TOKENS else 0
        right = Text()
        right.append(f"context {pct:.1f}% ({_fmt_k(used)}/{_fmt_k(MAX_AGENT_CONTEXT_TOKENS)})", style="dim")
        right.append("   ctrl+c quit \u00b7 /help", style="dim")
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
    if tool_name == "ask_l4_engineer":
        task = str(arguments.get("task", "")).strip()
        if task:
            preview = task if len(task) <= 120 else task[:117] + "..."
            return f"{role}: approve {tool_name}? Task: {preview}"
        return f"{role}: approve {tool_name}?"
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
