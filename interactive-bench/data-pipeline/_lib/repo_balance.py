"""Even, capacity-aware distribution of a target count across repos.

Used to keep the interactive-bench pipeline from over-sampling whichever
repos happen to have the "best" looking SWE-Chat sessions: given a total
target (e.g. 20 curated tasks) and N repos, each repo should get roughly
target // N, with any remainder going to the first repos in priority order
(e.g. 20 across 9 repos -> first 2 repos get 3, the rest get 2). Repos that
run out of candidates (hit their cap) give up their unused share to repos
that still have room, so the total still adds up to ``target`` whenever
enough capacity exists across the whole repo set.
"""
from __future__ import annotations


def allocate(total: int, order: list[str], caps: dict[str, int]) -> dict[str, int]:
    """Distribute ``total`` across ``order``, each capped by ``caps[repo]``.

    ``order`` also sets remainder priority: when ``total`` doesn't divide
    evenly, the earliest repos in ``order`` get the extra +1.
    """
    quotas = {repo: 0 for repo in order}
    remaining = max(0, total)
    active = [r for r in order if caps.get(r, 0) > 0]
    while remaining > 0 and active:
        base, extra = divmod(remaining, len(active))
        given = 0
        for i, repo in enumerate(active):
            want = base + (1 if i < extra else 0)
            if want <= 0:
                continue
            room = caps.get(repo, 0) - quotas[repo]
            take = min(want, room)
            quotas[repo] += take
            given += take
        remaining -= given
        active = [r for r in active if quotas[r] < caps.get(r, 0)]
        if given == 0:
            break
    return quotas
