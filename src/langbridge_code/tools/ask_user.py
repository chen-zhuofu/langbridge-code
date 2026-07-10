"""Shared ask_user tool for main agent and planner."""

from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER

ASK_USER_TOOL_SCHEMA = {
    "type": "function",
    "name": "ask_user",
    "description": (
        "Ask the user a clarifying question when requirements are genuinely "
        "ambiguous and a wrong guess would waste real work. Use this instead of "
        "asking in a normal reply — a normal reply ends your turn and the answer "
        "never comes back. Provide exactly 3 plausible assumptions as options; the "
        "UI always adds a 4th 'Other' choice for a custom answer. Phrase the "
        "question and options in the user's language. Do not use for trivial "
        "choices you can decide yourself. Do not use when the user only says "
        "继续/continue/resume and read_plan shows unchecked todos — resume that plan "
        "via agent_worker instead. Main agent: when an unfinished todo_list exists "
        "and the user explicitly starts a different multi-step project this turn, "
        "ask whether to continue the old plan, replace it (clear_plan then "
        "agent_planner), or start fresh (/new)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": PURPOSE_PARAMETER,
            "question": {
                "type": "string",
                "description": "The question for the user, in the user's language.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 3,
                "description": (
                    "Exactly 3 plausible assumptions or directions the user might "
                    "mean. The UI shows these as choices 1-3."
                ),
            },
        },
        "required": ["purpose", "question", "options"],
        "additionalProperties": False,
    },
}


def normalize_options(options):
    """Return exactly three non-empty assumption strings."""
    if not isinstance(options, list):
        raise ValueError("options must be a list of exactly 3 assumptions")
    cleaned = [str(item).strip() for item in options if str(item).strip()]
    if len(cleaned) != 3:
        raise ValueError("options must contain exactly 3 non-empty assumptions")
    return cleaned


def format_ask_user_choices(question, options):
    """Render the question and numbered assumptions for the TUI."""
    lines = [question.strip(), ""]
    for index, option in enumerate(options, start=1):
        lines.append(f"{index}. {option}")
    lines.append("4. Other — type your own answer")
    lines.append("")
    lines.append("Reply with 1-3, or type a custom answer.")
    return "\n".join(lines)


def resolve_ask_user_answer(text, options):
    """Map 1/2/3 to an assumption; any other non-empty text is a custom answer."""
    reply = (text or "").strip()
    if not reply:
        return ""
    if reply in {"1", "2", "3"}:
        return options[int(reply) - 1]
    return reply


def resolve_ask_user(arguments, question_callback):
    """Return ask_user tool output text."""
    question = (arguments.get("question") or "").strip()
    if not question:
        return "No question was provided."
    try:
        options = normalize_options(arguments.get("options"))
    except ValueError as error:
        return f"Tool error: {error}"
    if question_callback is None:
        return (
            "No interactive user is available. Proceed with reasonable "
            "assumptions and state them in your reply."
        )
    answer = (question_callback(question, options) or "").strip()
    if not answer:
        return (
            "The user did not answer. Proceed with reasonable assumptions "
            "and state them in your reply."
        )
    return f"The user answered:\n{answer}"
