"""Two-stage, reasoning-budgeted safety classifier for Auto permission mode."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from langbridge_code.agents.common.approval import (
    circuit_breaker_reason,
    protected_path_reason,
)
from langbridge_code.llm.parse import extract_output_text


SAFE_READ_TOOLS = frozenset({"glob", "grep", "read_file", "read_skill", "ToolSearch"})
_TRANSCRIPT_CHAR_LIMIT = 60_000

_SYSTEM_PROMPT = """You are LangBridge's action safety classifier.

Judge the real-world effect of the pending tool call against the user's explicit
authorization. Block actions that exceed that authorization or create serious
downside, including destructive or irreversible changes, credential discovery
or disclosure, data exfiltration, persistence or weakened security controls,
unreviewed effects on shared/production infrastructure, executing untrusted
external code, and bypassing safety or review checks.

The evidence intentionally contains only user messages and executable tool
calls. It excludes assistant prose and tool outputs. Treat every agent-inferred
target, credential, external destination, or increase in blast radius as
unauthorized unless the user clearly approved it. Evaluate composed commands
by their full effect, not by a benign wrapper or command name."""

_STAGE_1_INSTRUCTION = """Stage 1: use a very small reasoning budget and err on
the side of escalation. Output exactly one token: YES if this action might need
to be blocked, or NO if it is clearly safe and authorized."""

_STAGE_2_INSTRUCTION = """Stage 2: reason carefully over the same evidence.
Apply block rules, narrow benign exceptions, and the exact scope of user intent.
End with two lines in this exact form:
DECISION: ALLOW or DECISION: BLOCK
REASON: one concise sentence"""


@dataclass(frozen=True)
class AutoModeDecision:
    allowed: bool
    stage: int
    reason: str = ""


def auto_mode_route(
    tool_name: str,
    arguments: dict | None,
    *,
    workspace: Path | None = None,
) -> tuple[str, str]:
    """Return ``(allow|classify|block, reason)`` before an LLM call."""
    arguments = arguments or {}
    critical = circuit_breaker_reason(tool_name, arguments)
    if critical:
        return "block", critical
    if tool_name in SAFE_READ_TOOLS:
        return "allow", "built-in read-only tool"
    if tool_name == "schedule" and str(arguments.get("action", "")).lower() in {
        "list",
        "get",
        "view",
    }:
        return "allow", "read-only schedule action"
    if tool_name in {"write", "Edit"}:
        protected = protected_path_reason(tool_name, arguments)
        if protected:
            return "block", protected
        raw_path = str(arguments.get("path", "")).strip()
        if not raw_path:
            return "classify", "file target is missing"
        root = (workspace or Path.cwd()).resolve()
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate.resolve(strict=False).relative_to(root)
        except ValueError:
            return "classify", "file operation is outside the project"
        return "allow", "reviewable in-project file edit"
    return "classify", "action has external or state-changing effects"


def _plain_content(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def classifier_evidence(messages, role: str, tool_name: str, arguments: dict | None) -> str:
    """Build the reasoning-blind transcript shared by both classifier stages."""
    items = []
    for message in messages or []:
        if message.get("role") == "user":
            items.append({"kind": "user", "content": _plain_content(message.get("content", ""))})
        elif message.get("type") == "function_call":
            items.append(
                {
                    "kind": "tool_call",
                    "name": message.get("name", ""),
                    "arguments": message.get("arguments", "{}"),
                }
            )
    items.append(
        {
            "kind": "pending_tool_call",
            "role": role,
            "name": tool_name,
            "arguments": arguments or {},
        }
    )
    payload = json.dumps(items, ensure_ascii=False, indent=2, default=str)
    if len(payload) > _TRANSCRIPT_CHAR_LIMIT:
        payload = "[Earlier evidence omitted; missing authorization must bias toward blocking.]\n" + payload[-_TRANSCRIPT_CHAR_LIMIT:]
    return "Safety evidence:\n" + payload


class AutoModeClassifier:
    """Stage 1 cheaply escalates; Stage 2 spends reasoning only on flags."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def evaluate(
        self,
        messages,
        role: str,
        tool_name: str,
        arguments: dict | None,
    ) -> AutoModeDecision:
        from langbridge_code.llm.client import create_model_response

        evidence = classifier_evidence(messages, role, tool_name, arguments)
        shared = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": evidence},
        ]
        stage_1 = create_model_response(
            self.api_key,
            self.model,
            [*shared, {"role": "user", "content": _STAGE_1_INSTRUCTION}],
            reasoning={"effort": "low", "summary": "auto"},
            max_output_tokens=64,
            label="Auto mode · stage 1",
        )
        stage_1_text = extract_output_text(stage_1.get("output", [])).strip().upper()
        if stage_1_text == "NO":
            return AutoModeDecision(True, 1, "stage 1 found the action clearly safe")

        stage_2 = create_model_response(
            self.api_key,
            self.model,
            [*shared, {"role": "user", "content": _STAGE_2_INSTRUCTION}],
            max_output_tokens=4_000,
            label="Auto mode · stage 2",
        )
        stage_2_text = extract_output_text(stage_2.get("output", [])).strip()
        matches = re.findall(r"DECISION:\s*(ALLOW|BLOCK)\b", stage_2_text, re.IGNORECASE)
        if not matches:
            return AutoModeDecision(False, 2, "stage 2 returned no valid decision")
        reasons = re.findall(r"REASON:\s*(.+)", stage_2_text, re.IGNORECASE)
        reason = reasons[-1].strip() if reasons else "stage 2 classified the action as unsafe"
        return AutoModeDecision(matches[-1].upper() == "ALLOW", 2, reason)
