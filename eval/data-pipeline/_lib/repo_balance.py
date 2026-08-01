"""Deterministic, even allocation of a task target across repositories."""
from __future__ import annotations


def allocate(
    total: int,
    repos: list[str],
    *,
    minimums: dict[str, int] | None = None,
) -> dict[str, int]:
    """Return the most even ordered allocation totaling at least ``total``.

    Existing ``minimums`` cannot be undone. New slots go to the repository
    with the lowest allocation, using ``repos`` order to break ties.
    """
    minimums = minimums or {}
    quotas = {repo: max(0, int(minimums.get(repo, 0))) for repo in repos}
    remaining = max(0, total - sum(quotas.values()))

    while remaining:
        lowest = min(quotas.values())
        for repo in repos:
            if remaining == 0:
                break
            if quotas[repo] == lowest:
                quotas[repo] += 1
                remaining -= 1

    return quotas
