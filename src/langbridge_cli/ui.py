import json
import os
import threading

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, RichLog, Select, Static, TextArea

from langbridge_cli import control
from langbridge_cli.agent import run_agent
from langbridge_cli.config import COMPACT_WHEN_TOKENS_OVER, DEFAULT_MODEL, load_api_key
from langbridge_cli.context import estimate_tokens, restore_compacted_session_messages, restore_session_messages
from langbridge_cli.roles import SYSTEM_PROMPT
from langbridge_cli.session import (
    create_run_log_path,
    label_session,
    last_turn_id,
    list_session_logs,
    read_session_records,
    write_session_summary,
)


EMPTY_THOUGHT = " "
THEME = "tokyo-night"


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


class LangBridgeTui(App):
    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    #session_bar {
        height: 3;
        padding: 0 1;
        background: $panel;
    }

    #session_bar Button {
        margin: 0 1 0 0;
        min-width: 10;
        height: 3;
    }

    #session_select {
        width: 1fr;
        margin: 0 1 0 0;
    }

    #chat_log {
        height: 1fr;
        background: $surface;
        border: round $primary 40%;
        padding: 0 1;
    }

    #thought_toggle {
        width: 100%;
        height: 1;
        border: none;
        text-align: left;
        text-style: italic;
        color: $text-muted;
        background: $surface-darken-1;
    }

    #thought_toggle:hover {
        background: $boost;
    }

    #history_container {
        height: auto;
    }

    #thought_history {
        height: 10;
        background: $surface;
        border: round $secondary 50%;
        padding: 0 1;
    }

    #bottom_panel {
        dock: bottom;
        height: auto;
        background: $panel;
        border-top: tall $primary;
    }

    #approval_bar {
        height: auto;
        min-height: 5;
        padding: 1;
        background: $warning-darken-3;
        border: round $warning;
    }

    #approval_prompt {
        width: 1fr;
        padding: 1 0;
        color: $text;
    }

    #approve_button, #deny_button {
        width: 1fr;
        min-width: 12;
        margin-left: 1;
    }

    #input_row {
        height: auto;
        padding: 1;
    }

    #input {
        height: 5;
        width: 1fr;
        border: round $primary;
        background: $surface;
    }

    #input:focus {
        border: round $accent;
    }

    #send_button {
        height: 5;
        width: 12;
        margin-left: 1;
    }
    """

    BINDINGS = [
        ("ctrl+t", "toggle_thought_history", "Thoughts"),
        ("ctrl+a", "approve_pending", "Approve"),
        ("ctrl+d", "deny_pending", "Deny"),
        ("ctrl+p", "toggle_pause", "Pause"),
        ("ctrl+s", "stop", "Stop"),
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
        self.history_visible = False
        self.pending_approval = None
        self.always_approve = False
        self.turn_active = False
        self.turn_snapshot = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="session_bar"):
            yield Select(self.session_options(), id="session_select", prompt="Start new session")
            yield Button("Resume", id="resume_session_button")
            yield Button("Delete", id="delete_session_button", variant="error")
            yield Button("Always approve: off", id="always_approve_button")
            yield Button("Pause", id="pause_button", variant="warning")
            yield Button("Stop", id="stop_button", variant="error")
        yield RichLog(id="chat_log", wrap=True, highlight=True)
        yield Button(EMPTY_THOUGHT, id="thought_toggle")
        yield Container(RichLog(id="thought_history", wrap=True, highlight=True), id="history_container")
        with Vertical(id="bottom_panel"):
            with Horizontal(id="approval_bar"):
                yield Static("", id="approval_prompt")
                yield Button("Approve", id="approve_button", variant="success")
                yield Button("Deny", id="deny_button", variant="error")
            with Horizontal(id="input_row"):
                yield ChatInput(id="input")
                yield Button("Send", id="send_button", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "langbridge-cli"
        self.theme = THEME
        chat_log = self.query_one("#chat_log", RichLog)
        chat_log.border_title = "Chat"
        self.query_one("#thought_history", RichLog).border_title = "Thoughts & actions"
        input_box = self.query_one("#input", ChatInput)
        input_box.border_title = "Ask langbridge"
        input_box.border_subtitle = "Enter = send · Shift+Enter = newline"
        self.query_one("#thought_history", RichLog).display = False
        self.query_one("#approval_bar", Horizontal).display = False
        chat_log.write(f"langbridge-cli using {self.model}")
        self.start_new_session()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "thought_toggle":
            self.action_toggle_thought_history()
        elif event.button.id == "approve_button":
            self.resolve_approval(True)
        elif event.button.id == "deny_button":
            self.resolve_approval(False)
        elif event.button.id == "always_approve_button":
            self.toggle_always_approve()
        elif event.button.id == "pause_button":
            self.action_toggle_pause()
        elif event.button.id == "stop_button":
            self.action_stop()
        elif event.button.id == "send_button":
            self.submit_current_input()
        elif event.button.id == "resume_session_button":
            self.resume_selected_session()
        elif event.button.id == "delete_session_button":
            self.delete_selected_session()

    def action_toggle_thought_history(self) -> None:
        self.history_visible = not self.history_visible
        self.query_one("#thought_history", RichLog).display = self.history_visible

    def action_approve_pending(self) -> None:
        if self.pending_approval is not None:
            self.resolve_approval(True)

    def action_deny_pending(self) -> None:
        if self.pending_approval is not None:
            self.resolve_approval(False)

    def action_toggle_pause(self) -> None:
        if not self.turn_active:
            return
        if control.is_paused():
            control.resume()
            self.query_one("#pause_button", Button).label = "Pause"
            self.add_chat_line("Resumed.")
        else:
            control.pause()
            self.query_one("#pause_button", Button).label = "Resume"
            self.add_chat_line("Paused. The agent stops at the next step; press Resume to continue.")

    def action_stop(self) -> None:
        if not self.turn_active:
            return
        control.request_stop()
        # Unblock anything the worker is waiting on so it can unwind promptly.
        if self.pending_approval is not None:
            self.resolve_approval(False)
        self.query_one("#pause_button", Button).label = "Pause"
        self.add_chat_line("Stopping the agent...")

    def submit_current_input(self) -> None:
        if self.turn_active:
            return
        input_box = self.query_one("#input", ChatInput)
        text = input_box.text.strip()
        if not text:
            return
        input_box.text = ""
        if text == "/exit":
            self.exit()
            return

        self.query_one("#chat_log", RichLog).write(f"You: {text}")
        self.query_one("#thought_history", RichLog).clear()
        self.query_one("#thought_toggle", Button).label = EMPTY_THOUGHT
        input_box.disabled = True
        self.query_one("#send_button", Button).disabled = True
        control.clear_stop()
        control.resume()
        self.turn_active = True
        self.turn_snapshot = list(self.messages)
        self.run_worker(lambda: self.run_turn(text), thread=True)

    def run_turn(self, text):
        self.turn_id += 1
        if estimate_tokens(self.messages) > COMPACT_WHEN_TOKENS_OVER:
            self.messages = restore_compacted_session_messages(read_session_records(self.run_log_path))
            self.call_from_thread(self.add_chat_line, "(compacted older context to stay under the token budget)")

        self.messages.append({"role": "user", "content": text})
        try:
            reply = run_agent(
                self.api_key,
                self.model,
                self.messages,
                self.run_log_path,
                self.turn_id,
                trace_sink=self.post_trace_event,
                print_reply=False,
                approval_callback=self.request_approval,
            )
        except control.StopRequested:
            self.call_from_thread(self.finish_stopped)
            return
        write_session_summary(self.api_key, self.model, self.run_log_path)
        self.call_from_thread(self.finish_turn, reply or "")

    def post_trace_event(self, event):
        self.call_from_thread(self.add_trace_event, event)

    def add_trace_event(self, event):
        line = format_trace_event(event)
        self.query_one("#thought_history", RichLog).write(line)
        current_thought = format_current_thought(event)
        if current_thought:
            self.query_one("#thought_toggle", Button).label = current_thought

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
        self.query_one("#approval_prompt", Static).update(f"{summary}\n{details}")
        self.query_one("#thought_toggle", Button).label = summary
        self.query_one("#thought_history", RichLog).write(summary)
        self.add_chat_line(f"Approval required: {summary}")
        if details:
            self.add_chat_line(details)
        self.query_one("#approval_bar", Horizontal).display = True
        self.query_one("#approve_button", Button).focus()
        shown.set()

    def resolve_approval(self, approved):
        if self.pending_approval is None:
            return
        decision, ready = self.pending_approval
        decision["approved"] = approved
        self.pending_approval = None
        self.query_one("#approval_bar", Horizontal).display = False
        self.query_one("#approval_prompt", Static).update("")
        self.add_chat_line("Approved." if approved else "Denied.")
        ready.set()

    def add_chat_line(self, line):
        self.query_one("#chat_log", RichLog).write(line)

    def finish_turn(self, reply):
        if reply:
            self.add_chat_line(f"Assistant: {reply}")
        self.reset_after_turn()

    def finish_stopped(self):
        # Discard the partial turn so the model history stays valid.
        if self.turn_snapshot is not None:
            self.messages = self.turn_snapshot
        self.add_chat_line("Stopped.")
        self.reset_after_turn()

    def reset_after_turn(self):
        self.turn_active = False
        self.turn_snapshot = None
        control.clear_stop()
        control.resume()
        self.query_one("#pause_button", Button).label = "Pause"
        self.query_one("#thought_toggle", Button).label = EMPTY_THOUGHT
        self.query_one("#approval_bar", Horizontal).display = False
        self.query_one("#approval_prompt", Static).update("")
        self.query_one("#send_button", Button).disabled = False
        input_box = self.query_one("#input", ChatInput)
        input_box.disabled = False
        input_box.focus()

    def session_options(self):
        return [(label_session(path), str(path)) for path in self.session_logs]

    def selected_session_path(self):
        value = self.query_one("#session_select", Select).value
        if value == Select.BLANK:
            return None
        return next((path for path in self.session_logs if str(path) == value), None)

    def start_new_session(self):
        self.run_log_path = create_run_log_path()
        self.turn_id = 0
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.add_chat_line(f"Agent loop log: {self.run_log_path}")

    def resume_selected_session(self):
        path = self.selected_session_path()
        if path is None:
            self.start_new_session()
            return
        records = read_session_records(path)
        self.messages = restore_session_messages(records) or [{"role": "system", "content": SYSTEM_PROMPT}]
        self.run_log_path = path
        self.turn_id = last_turn_id(records)
        self.query_one("#chat_log", RichLog).clear()
        self.add_chat_line(f"langbridge-cli using {self.model}")
        self.add_chat_line(f"Agent loop log: {self.run_log_path}")
        self.add_chat_line(f"Resumed: {label_session(path)}")

    def delete_selected_session(self):
        path = self.selected_session_path()
        if path is None:
            return
        try:
            path.unlink()
        except OSError as error:
            self.add_chat_line(f"Could not delete session: {error}")
            return
        self.session_logs = [item for item in self.session_logs if item != path]
        self.query_one("#session_select", Select).set_options(self.session_options())
        self.add_chat_line(f"Deleted session: {path.name}")

    def toggle_always_approve(self):
        self.always_approve = not self.always_approve
        status = "on" if self.always_approve else "off"
        self.query_one("#always_approve_button", Button).label = f"Always approve: {status}"


def format_trace_event(event):
    marker = "↳ " if event.kind == "action" else ""
    return f"{event.role}: {marker}{event.text}"


def format_current_thought(event):
    if event.kind != "thought":
        return ""
    return f"{event.role}: {event.text}"


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
    compact = json.dumps(arguments, ensure_ascii=False, indent=2)
    if len(compact) > 600:
        compact = compact[:597] + "..."
    return compact


def run_tui():
    LangBridgeTui().run()
