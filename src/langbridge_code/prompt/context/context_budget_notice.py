# Transient per-request budget notice (appended at the request tail only).
CONTEXT_BUDGET_NOTICE_PREFIX = (
    "[CONTEXT_STATUS] Automated per-step status (not from the human):"
)

CONTEXT_BUDGET_BODY = (
    "There is no hard context stop — the loop ends on time or step limits only. "
    "When usage crosses the compact threshold, older rounds are dropped and only "
    "the most recent rounds stay in your transcript. "
    "Record important findings in files, the plan, or progress notes rather than "
    "relying on old transcript text staying verbatim."
)

CONTEXT_BUDGET_NEAR_LIMIT = (
    "Context usage is high — older rounds are being dropped; persist anything "
    "you must keep verbatim."
)
