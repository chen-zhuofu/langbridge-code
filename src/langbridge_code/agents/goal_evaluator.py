"""Goal evaluator agent — judges whether LangBridge satisfied a completion condition."""
from __future__ import annotations

import json
from dataclasses import dataclass

from langbridge_code.agents.common.todo_list import artifact_plan_path
from langbridge_code.agents.common.workspace import plan_file_scope
from langbridge_code.context.message import recent_chat_turns
from langbridge_code.context.foreground import ForegroundTracker
from langbridge_code.settings import GOAL_EVAL_INPUT_CHARS, GOAL_EVALUATOR_MAX_STEPS
from langbridge_code.tools import GOAL_VERIFICATION_TOOL_SCHEMAS, GOAL_VERIFICATION_TOOLS
from langbridge_code.tools.common.purpose import without_purpose
from langbridge_code.llm.client import create_model_response
from langbridge_code.llm.parse import extract_output_text, print_step_trace

EVALUATOR_PROMPT = """You are the Goal Evaluator for LangBridge Code — a skeptical second-opinion reviewer.

A separate LangBridge agent is working toward a completion condition. You judge
whether that condition is satisfied. You did not do the work yourself and you
should not trust the agent's own assessment without evidence.

You have the same verification tools as the main agent: read files, grep, list
directories, run bash (tests, builds, curl), read_webpage, and read_skill when a
playbook helps your check. Use them to verify independently — transcript claims
are hints, not proof.

Do the following every time:
1. Read the completion condition.
2. If a plan file exists (`todo_list.md` in the current session artifacts),
   read_file it by its virtual path `todo_list.md` —
   check Desired end state and Success criteria.
3. Gather evidence with tools (run verify commands, inspect files, check live URLs).
4. Decide whether the condition is genuinely satisfied.

Plausibility is not correctness. Announcing success without evidence is
NEEDS_WORK. Missing evidence for any part of the condition is NEEDS_WORK. If
you find yourself assuming something probably works, stop and verify with tools.

Do not implement features, edit files, or delegate subagents — verify only.

Begin your reply with the bare word PASS or NEEDS_WORK on its own line, with
nothing before it. Then:
- PASS: one line stating what evidence convinced you.
- NEEDS_WORK: a bullet list of specific, fixable findings the LangBridge agent
  should act on next."""


@dataclass(frozen=True)
class GoalVerdict:
    met: bool
    reason: str
    guidance: str = ""


class GoalEvaluatorAgent:
    """Dedicated evaluator that watches LangBridge and drives continuation."""

    label = "Goal evaluator"

    def __init__(self, api_key, model, *, run_log_path=None, trace_sink=None):
        self.api_key = api_key
        self.model = model
        self.run_log_path = run_log_path
        self.trace_sink = trace_sink

    def evaluate(self, condition: str, messages) -> GoalVerdict:
        transcript = _goal_transcript(messages)
        user_content = (
            f"Completion condition:\n{condition}\n\n"
            f"Conversation transcript:\n{transcript}"
        )
        agent_messages = [
            {"role": "system", "content": EVALUATOR_PROMPT},
            {"role": "user", "content": user_content},
        ]
        foreground = ForegroundTracker(self.label, agent_messages, self.model)
        foreground.activate()
        last_output = []
        try:
            for _ in range(GOAL_EVALUATOR_MAX_STEPS):
                foreground.publish()
                response = create_model_response(
                    self.api_key,
                    self.model,
                    agent_messages,
                    tool_schemas=GOAL_VERIFICATION_TOOL_SCHEMAS,
                    label=self.label,
                    stream_sink=self.trace_sink,
                )
                output = response.get("output", [])
                last_output = output
                tool_calls = [item for item in output if item.get("type") == "function_call"]
                if not tool_calls:
                    print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                    text = extract_output_text(output)
                    return _parse_verdict(text)
                print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                for item in output:
                    agent_messages.append(item)
                for call in tool_calls:
                    agent_messages.append(self._run_tool(call))
                foreground.publish()

            print_step_trace(last_output, include_message=True, label=self.label, sink=self.trace_sink)
            text = extract_output_text(last_output)
            if text.strip():
                return _parse_verdict(text)
            return GoalVerdict(
                met=False,
                reason="Evaluator stopped: max steps without a verdict.",
                guidance="Continue working and surface verifiable evidence for the goal.",
            )
        finally:
            foreground.deactivate()

    def _run_tool(self, call):
        name = call.get("name")
        call_id = call.get("call_id")
        try:
            arguments = without_purpose(json.loads(call.get("arguments") or "{}"))
            if name not in GOAL_VERIFICATION_TOOLS:
                raise ValueError(f"Unknown evaluator tool: {name}")
            with plan_file_scope(artifact_plan_path(self.run_log_path)):
                output = GOAL_VERIFICATION_TOOLS[name](**arguments)
        except Exception as error:
            output = f"Tool error: {error}"
        return {"type": "function_call_output", "call_id": call_id, "output": output}


def _goal_transcript(messages, *, max_chars: int = GOAL_EVAL_INPUT_CHARS) -> str:
    turns = recent_chat_turns(messages, max_turns=40, max_chars=max_chars)
    if not turns:
        return "(no conversation yet)"
    lines = []
    used = 0
    for turn in turns:
        role = "User" if turn["role"] == "user" else "Assistant"
        chunk = f"{role}: {turn['content']}"
        if used + len(chunk) > max_chars:
            break
        lines.append(chunk)
        used += len(chunk) + 1
    return "\n\n".join(lines)


def _parse_verdict(text: str) -> GoalVerdict:
    stripped = (text or "").strip()
    if not stripped:
        return GoalVerdict(met=False, reason="Evaluator returned empty response.", guidance="Continue working.")

    lines = stripped.splitlines()
    verdict_line = lines[0].strip().upper()
    body = "\n".join(lines[1:]).strip()
    if verdict_line == "PASS":
        reason = body.split("\n", 1)[0].strip() if body else "Condition met."
        return GoalVerdict(met=True, reason=reason or "Condition met.", guidance="")
    if verdict_line == "NEEDS_WORK":
        guidance = body or "Continue working toward the goal with tools until the condition is verifiable."
        return GoalVerdict(met=False, reason="Condition not met yet.", guidance=guidance)

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end <= start:
            return GoalVerdict(met=False, reason=stripped[:500], guidance="Continue working.")
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return GoalVerdict(met=False, reason=stripped[:500], guidance="Continue working.")
    met = bool(payload.get("met"))
    reason = str(payload.get("reason") or "").strip()
    guidance = str(payload.get("guidance") or "").strip()
    if not reason:
        reason = "Condition met." if met else "Condition not met yet."
    if not met and not guidance:
        guidance = "Continue working toward the goal with tools until the condition is verifiable."
    return GoalVerdict(met=met, reason=reason, guidance=guidance)
