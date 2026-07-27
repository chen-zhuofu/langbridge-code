"""Loop guards: every loop ends on whichever limit trips first.

Three independent limits keep loops from running forever:
  1. max loop count  -- handled by the callers' range()/round caps,
  2. wall-clock time  -- over_time_budget(),
  3. context size     -- over_context_budget(), for loops that call the LLM.
"""

import time

from langbridge_code.context.common.budget import estimate_tokens


def now():
    return time.monotonic()


def over_time_budget(start, max_seconds):
    if max_seconds is None:
        return False
    return (now() - start) >= max_seconds


def over_context_budget(messages, max_tokens):
    if max_tokens is None:
        return False
    return estimate_tokens(messages) >= max_tokens
