import json
import os
import threading

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Select, Static

from langbridge_cli.agent import run_agent
from langbridge_cli.config import COMPACT_WHEN_TOKENS_OVER, DEFAULT_MODEL, load_api_key
from langbridge_cli.context import estimate_tokens, restore_compacted_session_messages, restore_session_messages
from langbridge_cli.prompt import SYSTEM_PROMPT
from langbridge_cli.session import (
    create_run_log_path,
    label_session,
    last_turn_id,
    list_session_logs,
    read_session_records,
    write_session_summary,
)


EMPTY_THOUGHT = " "


class LangBridgeTui(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #session_bar {
        height: auto;
        min-height: 3;
        padding: 0 1;
    }

    #session_select {
        width: 1fr;
    }

    #chat_log {
        height: 1fr;
        border: round $surface;
    }

    #thought_toggle {
        width: 100%;
        height: 3;
        text-align: left;
        color: $text-muted;
    }

    #thought_history {
        height: 10;
        border: round $surface;
    }

    #bottom_panel {
        dock: bottom;
        height: auto;
        background: $surface;
        border-top: tall $primary;
    }

    #approval_bar {
        height: auto;
        min-height: 5;
        padding: 0 1;
        background: $warning-darken-3;
        border-bottom: tall $warning;
    }

    #approval_prompt {
        width: 1fr;
        padding: 1 0;
        color: $text;
    }

    #approve_button, #deny_button {
        width: 1fr;
        min-width: 12;
    }

    #input {
        height: 3;
        margin: 0 1 1 1;
    }
    """

    BINDINGS = [
        ("ctrl+t", "toggle_thought_history", "Thoughts"),
        ("ctrl+a", "approve_pending", "Approve"),
        ("ctrl+d", "deny_pending", "Deny"),
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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="session_bar"):
            yield Select(self.session_options(), id="session_select", prompt="Start new session")
            yield Button("Resume", id="resume_session_button")
            yield Button("Delete", id="delete_session_button", variant="error")
            yield Button("Always approve: off", id="always_approve_button")
        yield RichLog(id="chat_log", wrap=True, highlight=True)
        yield Button(EMPTY_THOUGHT, id="thought_toggle")
        yield Container(RichLog(id="thought_history", wrap=True, highlight=True), id="history_container")
        with Vertical(id="bottom_panel"):
            with Horizontal(id="approval_bar"):
                yield Static("", id="approval_prompt")
                yield Button("Approve", id="approve_button", variant="success")
                yield Button("Deny", id="deny_button", variant="error")
            yield Input(placeholder="Ask langbridge...", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "langbridge-cli"
        self.query_one("#thought_history", RichLog).display = False
        self.query_one("#approval_bar", Horizontal).display = False
        self.query_one("#chat_log", RichLog).write(f"langbridge-cli using {self.model}")
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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if text == "/exit":
            self.exit()
            return

        self.query_one("#chat_log", RichLog).write(f"You: {text}")
        self.query_one("#thought_history", RichLog).clear()
        self.query_one("#thought_toggle", Button).label = EMPTY_THOUGHT
        event.input.disabled = True
        self.run_worker(lambda: self.run_turn(text), thread=True)

    def run_turn(self, text):
        self.turn_id += 1
        if estimate_tokens(self.messages) > COMPACT_WHEN_TOKENS_OVER:
            self.messages = restore_compacted_session_messages(read_session_records(self.run_log_path))
            self.call_from_thread(self.add_chat_line, "(compacted older context to stay under the token budget)")

        self.messages.append({"role": "user", "content": text})
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
        self.query_one("#thought_toggle", Button).label = EMPTY_THOUGHT
        self.query_one("#approval_bar", Horizontal).display = False
        self.query_one("#approval_prompt", Static).update("")
        self.query_one("#input", Input).disabled = False
        self.query_one("#input", Input).focus()

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
