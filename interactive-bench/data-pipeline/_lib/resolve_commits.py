"""Resolve first/last/gold/base commits for a SWE-Chat session.

Table path:
  session.checkpoint_ids → checkpoints.commit_shas → commits (by date)

Gold = reachable from default-branch HEAD among candidates.
Base = git parent of the earliest commit on the chain to gold (caller supplies
parent lookup / reachability via callbacks so this module stays pure).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence


def _parse_json_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x]
        return [str(parsed)]
    return []


def _as_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # treat as unix seconds
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class CommitRef:
    sha: str
    commit_date: datetime | None = None
    checkpoint_pk: str | None = None
    commit_index: int | None = None


@dataclass
class ResolveResult:
    ok: bool
    candidates: list[CommitRef] = field(default_factory=list)
    first_sha: str | None = None
    last_sha: str | None = None
    gold_sha: str | None = None
    base_sha: str | None = None
    reason: str = ""


def collect_candidate_commits(
    checkpoint_ids: Sequence[str] | str | None,
    *,
    checkpoint_commit_shas: dict[str, Sequence[str]],
    commit_meta: dict[str, dict] | None = None,
) -> list[CommitRef]:
    """Union commit SHAs across all session checkpoints, de-duped by SHA."""
    cps = _parse_json_list(checkpoint_ids)
    seen: dict[str, CommitRef] = {}
    meta = commit_meta or {}
    for cp in cps:
        for sha in _parse_json_list(list(checkpoint_commit_shas.get(cp, []))):
            if sha in seen:
                continue
            row = meta.get(sha) or {}
            seen[sha] = CommitRef(
                sha=sha,
                commit_date=_as_datetime(row.get("commit_date") or row.get("author_date")),
                checkpoint_pk=str(row.get("checkpoint_pk") or cp),
                commit_index=row.get("commit_index"),
            )
    refs = list(seen.values())
    refs.sort(
        key=lambda r: (
            r.commit_date or datetime.min,
            r.commit_index if r.commit_index is not None else -1,
            r.sha,
        )
    )
    return refs


def pick_by_date(refs: Sequence[CommitRef], *, which: str) -> CommitRef | None:
    if not refs:
        return None
    ordered = sorted(
        refs,
        key=lambda r: (
            r.commit_date or datetime.min,
            r.commit_index if r.commit_index is not None else -1,
            r.sha,
        ),
    )
    return ordered[0] if which == "first" else ordered[-1]


IsAncestor = Callable[[str, str], bool]
"""is_ancestor(sha, tip) → True if sha is ancestor of tip (or equal)."""

ParentOf = Callable[[str], str | None]
"""parent_of(sha) → first parent SHA or None."""


def resolve_gold_and_base(
    refs: Sequence[CommitRef],
    *,
    default_branch_tip: str,
    is_ancestor: IsAncestor,
    parent_of: ParentOf,
    max_span_commits: int | None = None,
) -> ResolveResult:
    """Pick gold (reachable from tip) and base (parent of earliest chain commit).

    When ``max_span_commits`` is set and the gold-line session span is longer,
    base is taken from the parent of the *latest* ``max_span_commits`` session
    commits only. Long chats that end at tip otherwise swallow unrelated work.
    """
    if not refs:
        return ResolveResult(ok=False, reason="no commits for session")
    if not default_branch_tip:
        return ResolveResult(ok=False, candidates=list(refs), reason="missing default_branch tip")

    first = pick_by_date(refs, which="first")
    last = pick_by_date(refs, which="last")
    reachable = [r for r in refs if is_ancestor(r.sha, default_branch_tip)]
    if not reachable:
        return ResolveResult(
            ok=False,
            candidates=list(refs),
            first_sha=first.sha if first else None,
            last_sha=last.sha if last else None,
            reason="no session commit reachable from default branch",
        )

    gold = pick_by_date(reachable, which="last")
    assert gold is not None

    # Earliest reachable commit that is still an ancestor of gold (same line of work).
    on_gold_line = [r for r in reachable if is_ancestor(r.sha, gold.sha)]
    if not on_gold_line:
        on_gold_line = [gold]
    ordered = sorted(
        on_gold_line,
        key=lambda r: (
            r.commit_date or datetime.min,
            r.commit_index if r.commit_index is not None else -1,
            r.sha,
        ),
    )
    if max_span_commits is not None and max_span_commits > 0 and len(ordered) > max_span_commits:
        ordered = ordered[-max_span_commits:]
    earliest = ordered[0]

    base = parent_of(earliest.sha)
    if not base:
        return ResolveResult(
            ok=False,
            candidates=list(refs),
            first_sha=first.sha if first else None,
            last_sha=last.sha if last else None,
            gold_sha=gold.sha,
            reason=f"cannot resolve parent of earliest commit {earliest.sha[:12]}",
        )

    return ResolveResult(
        ok=True,
        candidates=list(refs),
        first_sha=first.sha if first else None,
        last_sha=last.sha if last else None,
        gold_sha=gold.sha,
        base_sha=base,
        reason="",
    )


def files_outside_touched(
    changed_files: Iterable[str],
    files_touched: Iterable[str] | str | None,
) -> list[str]:
    """Return changed paths not listed in session files_touched (dirty rebase signal)."""
    touched = set(_parse_json_list(files_touched))
    if not touched:
        return []
    dirty = []
    for path in changed_files:
        p = str(path).lstrip("./")
        if p and p not in touched and f"./{p}" not in touched:
            # allow prefix match: touched dir vs file
            if not any(p == t or p.startswith(t.rstrip("/") + "/") for t in touched):
                dirty.append(p)
    return dirty
