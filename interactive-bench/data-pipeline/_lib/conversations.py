"""Load SWE-Chat conversation / prompt turns for a session."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from _lib.quality import clean_user_text


def _parse_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            return [text]
    return []


@lru_cache(maxsize=4)
def _conversations_path_mtime(path_str: str) -> float:
    path = Path(path_str)
    return path.stat().st_mtime if path.exists() else 0.0


def load_conversation_turns(data_dir: Path, session_id: str) -> list[dict[str, Any]]:
    """Return conversation rows for ``session_id`` sorted by timestamp if present.

    Uses a parquet row-group / predicate filter when possible so we do not load
    the full multi-GB conversations table for every session.
    """
    path = Path(data_dir) / "conversations.parquet"
    if not path.exists():
        return []

    # Touch cache key so path changes invalidate helpers that depend on it.
    _conversations_path_mtime(str(path.resolve()))

    try:
        import pyarrow.parquet as pq

        table = pq.read_table(
            path,
            filters=[("session_id", "==", str(session_id))],
        )
        rows = table.to_pylist()
    except Exception:
        import pandas as pd

        df = pd.read_parquet(path, filters=[("session_id", "==", str(session_id))])
        if df.empty and "session_id" in getattr(pd.read_parquet(path, columns=["session_id"]), "columns", []):
            # filters unsupported — last resort full scan (slow)
            df = pd.read_parquet(path)
            df = df[df["session_id"].astype(str) == str(session_id)]
        rows = df.to_dict(orient="records")

    if not rows:
        return []
    rows.sort(
        key=lambda r: str(
            r.get("timestamp")
            or r.get("created_at")
            or r.get("conversation_turn_number")
            or ""
        )
    )
    return rows


def user_prompts_from_turns(turns: list[dict[str, Any]]) -> list[str]:
    """Extract user-facing prompt texts in order."""
    prompts: list[str] = []
    for row in turns:
        # Prefer clean conversational rows when the flag exists.
        if row.get("is_conversational") is False:
            continue
        role = str(row.get("role") or row.get("speaker") or "").lower()
        text = row.get("content") or row.get("text") or row.get("prompt") or ""
        if isinstance(text, (list, dict)):
            text = json.dumps(text, ensure_ascii=False)
        text = clean_user_text(str(text))
        if not text:
            continue
        if role in {"user", "human", "prompt"} or (
            not role and row.get("is_user") is True
        ):
            prompts.append(text)
    return prompts


def first_user_prompt(turns: list[dict[str, Any]], fallback: str = "") -> str:
    prompts = user_prompts_from_turns(turns)
    return prompts[0] if prompts else fallback


def session_prompt_intents(row_or_inst: dict[str, Any]) -> list[str]:
    """Best-effort read of SWE-Chat prompt_intent labels if present."""
    for key in ("prompt_intents", "prompt_intent", "intents_raw"):
        vals = _parse_list(row_or_inst.get(key))
        if vals:
            return [str(v) for v in vals]
    return []
