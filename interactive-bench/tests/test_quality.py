"""Quality gates: instruction cleanup, meta intents, oracle alignment."""
from __future__ import annotations

import sys
from pathlib import Path

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib.quality import (  # noqa: E402
    clean_user_text,
    f2p_fingerprint,
    filter_intents,
    is_meta_intent,
    oracle_aligned,
)


def test_pick_instruction_skips_image_only():
    from _lib.quality import pick_instruction

    instruction, followups = pick_instruction(
        ["Let's make this 1 GB\n[Image: image/png]", "Increase file size limit to 1 GB"]
    )
    assert "Increase file size" in instruction
    assert followups == []


def test_clean_user_text_strips_image_placeholders():
    text = "Let's make this 1 GB\n[Image: image/png]\nThanks"
    assert "[Image" not in clean_user_text(text)
    assert "1 GB" in clean_user_text(text)


def test_meta_intent_filters_commit_push():
    assert is_meta_intent("Commit the restructured changes to git.")
    assert is_meta_intent("Push the committed changes to the remote repository.")
    assert not is_meta_intent("Add a dark/light mode toggle")


def test_filter_intents_drops_meta_and_caps():
    raw = [
        {"id": "i1", "text": "Restructure frontend", "source_turn": 0, "revealed_at_start": True},
        {"id": "i2", "text": "Commit the changes", "source_turn": 1, "revealed_at_start": False},
        {"id": "i3", "text": "Push to remote", "source_turn": 2, "revealed_at_start": False},
    ]
    out = filter_intents(raw)
    assert len(out) == 1
    assert out[0]["id"] == "i1"
    assert out[0]["revealed_at_start"] is True


def test_oracle_aligned_detects_mismatch():
    assert oracle_aligned(
        instruction="Add dark/light theme toggle",
        intents=[{"text": "Add dark mode button"}],
        test_files=["backend/tests/test_session_manager.py"],
        fail_to_pass=["backend/tests/test_session_manager.py::test_heartbeat"],
        test_patch="def test_heartbeat():\n    pass\n",
    ) is False
    assert oracle_aligned(
        instruction="Increase file size limit and checkpoint embedding batches",
        intents=[{"text": "checkpoint saves and resumes"}],
        test_files=["tests/test_embedding.py"],
        fail_to_pass=[
            "tests/test_embedding.py::TestEmbedTextsInBatches::test_checkpoint_saves_and_resumes"
        ],
        test_patch="+def test_checkpoint_saves_and_resumes",
    ) is True


def test_f2p_fingerprint_stable():
    a = f2p_fingerprint(["t::b", "t::a"], repo="org/repo")
    b = f2p_fingerprint(["t::a", "t::b"], repo="org/repo")
    assert a == b
    assert a != f2p_fingerprint(["t::a", "t::b"], repo="other/repo")
