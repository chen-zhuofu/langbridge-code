"""Strip terminal control noise that can leak into TUI text inputs over SSH."""
import re

# CSI / OSC sequences (including SGR mouse reporting with ESC prefix).
_CSI_OR_OSC_RE = re.compile(
    r"\x1b(?:\[[\d;?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)
# Partial or full SGR mouse reporting, e.g. ESC [<35;223;12M or [<35;223;12M
_MOUSE_SGR_RE = re.compile(r"(?:\x1b)?\[<[\d;]+[Mm]")
# Password prompts accidentally echoed into the input widget.
_SUDO_PROMPT_RE = re.compile(r"\[sudo\]\s*password\s+for\s+\S+\s*:?", re.IGNORECASE)


def strip_terminal_control_text(text: str) -> str:
    """Remove mouse/ANSI control sequences from user-visible input text."""
    if not text:
        return text
    cleaned = _CSI_OR_OSC_RE.sub("", text)
    cleaned = _MOUSE_SGR_RE.sub("", cleaned)
    cleaned = _SUDO_PROMPT_RE.sub("", cleaned)
    return cleaned


def is_terminal_control_only(text: str) -> bool:
    """True when text is empty or only terminal control noise."""
    return not strip_terminal_control_text(text or "").strip()
