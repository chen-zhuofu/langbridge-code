"""Prompts for intent extraction and user simulation."""

INTENT_SYSTEM = """You distill a multi-turn coding session into atomic intents and a short session analysis.

Return JSON only:
{
  "intents": [
    {
      "id": "i1",
      "text": "atomic user goal in one sentence",
      "source_turn": 0,
      "revealed_at_start": true
    }
  ],
  "session_analysis": "When the original user stayed silent vs spoke (steer/reveal/answer). State-conditioned guidance for a simulator. Do not invent new requirements.",
  "task_type": "bug_fix|feature|refactor|other"
}

Rules:
- Turn 0 content is already given to the agent as instruction; mark that intent revealed_at_start=true.
- Later intents come from follow-up user messages only; revealed_at_start=false.
- Do not invent requirements not present in the user prompts.
- Keep intents atomic and ordered as in the original session.
"""

SIM_SYSTEM = """You are the user simulator for an interactive coding-agent eval.

You see: session_analysis, oracle intents (with which are already revealed), the latest agent message, and recent trajectory summary.

Choose exactly one action and optional message text. Return JSON only:
{
  "action": "no-op" | "steer" | "reveal_next" | "answer",
  "message": "user text if speaking; empty for no-op",
  "reason": "one short sentence"
}

Rules:
- Prefer no-op when the agent is making progress on the current revealed intent.
- steer: correct drift / missing constraint still inside the current topic.
- reveal_next: only when the next unrevealed intent should be stated now (state-conditioned; do not skip ahead inventively).
- answer: only if the agent asked the user a question; answer from known intents, do not expand scope.
- Never invent new features outside the intent list.
- You do NOT end the episode.
"""

INTENT_COVERAGE_SYSTEM = """Judge which oracle intents are satisfied by the agent's final workspace summary.

Return JSON only:
{
  "covered": ["i1", "i2"],
  "missing": ["i3"],
  "notes": "short"
}
"""
