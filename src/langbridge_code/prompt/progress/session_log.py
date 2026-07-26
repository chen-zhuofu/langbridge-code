"""Prompts for compacting / summarizing session progress.md."""

PROGRESS_MERGE_SYSTEM = "You merge session progress notes."

PROGRESS_TURN_SUMMARY_SYSTEM = "You write terse session progress notes."


def progress_merge_user_prompt(heading: str, source: str) -> str:
    return (
        "Merge these session progress turn sections into ONE concise section.\n"
        f"Use this exact heading as the first line: {heading}\n"
        "Keep factual bullets about work done, files, tests, and open items.\n"
        "Drop redundancy. No preamble.\n\n"
        f"{source}"
    )


def progress_turn_summary_user_prompt(source: str) -> str:
    return (
        "Write concise progress bullets for the main coding agent's session log.\n"
        "Audience: the same agent on the next user turn. Be factual and specific.\n"
        "Keep **In:** and **Out:** lines exactly as provided. Only add bullet lines "
        "between them summarizing delegated work, files/tests, outcomes, open items.\n"
        "Format under the ## Turn N heading. No preamble.\n\n"
        f"{source}"
    )
