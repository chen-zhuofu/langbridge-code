CONTEXT_BUDGET_MARKER = "\n\n---\nContext status (updated each step):"

CONTEXT_BUDGET_BODY = (
    "There is no hard context stop — the loop ends on time or step limits only. "
    "When usage crosses the compact threshold, everything except the most recent "
    "rounds is compressed automatically into one prose summary, so older raw "
    "detail may disappear from your transcript. "
    "Record important findings in files, the plan, or progress notes rather than "
    "relying on old transcript text staying verbatim."
)

CONTEXT_BUDGET_NEAR_LIMIT = (
    "Context usage is high — older rounds are being compacted; persist anything "
    "you must keep verbatim."
)
