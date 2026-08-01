"""End-to-end: enrich → (intent inside curate) → stub eval (all offline)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

_INTERACTIVE = Path(__file__).resolve().parents[1]
_PIPELINE = _INTERACTIVE / "data-pipeline"
for _p in (_PIPELINE, _INTERACTIVE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _lib.spec import build_interactive_spec  # noqa: E402
from enrich.enrich import enrich_one  # noqa: E402
from harness.agent import make_stub_agent  # noqa: E402
from harness.score import score_episode  # noqa: E402
from harness.sim import run_episode  # noqa: E402
from intent.analyze import analyze_one  # noqa: E402

DIFF = """diff --git a/pkg/widgetmod.py b/pkg/widgetmod.py
index 111..222 100644
--- a/pkg/widgetmod.py
+++ b/pkg/widgetmod.py
@@ -1 +1 @@
-return 1
+return 2
diff --git a/tests/test_widgetmod.py b/tests/test_widgetmod.py
index 333..444 100644
--- a/tests/test_widgetmod.py
+++ b/tests/test_widgetmod.py
@@ -1 +1 @@
-assert False
+assert True
"""


def test_offline_pipeline_to_stub_eval(tmp_path):
    resolved = {
        "task_id": "acme__widgets__feedface",
        "session_id": "feedface-0001",
        "repo": "acme/widgets",
        "base_commit": "b" * 40,
        "gold_commit": "g" * 40,
        "files_touched": ["pkg/widgetmod.py", "tests/test_widgetmod.py"],
        "prompt_count": 2,
        "prompt_intents": ["Create new code"],
        "instruction": "",
    }
    turns = [
        {
            "timestamp": "2026-01-01T10:00:00Z",
            "role": "user",
            "content": "Change widgetmod to return 2",
        },
        {
            "timestamp": "2026-01-01T10:00:40Z",
            "role": "assistant",
            "content": "working",
        },
        {
            "timestamp": "2026-01-01T10:01:10Z",
            "role": "user",
            "content": "Update the widgetmod unit test too",
        },
    ]

    with patch("enrich.enrich.compare_diff", return_value=DIFF), patch(
        "enrich.enrich.load_conversation_turns", return_value=turns
    ), patch("enrich.enrich.compare_commits", return_value=[]), patch(
        "enrich.enrich.compare_ahead_by", return_value=1
    ):
        enriched = enrich_one(resolved, data_dir=tmp_path)
    assert not enriched.get("_drop"), enriched.get("reason")
    assert enriched["instruction"] == "Change widgetmod to return 2"
    assert enriched["task_type"] == "feature"
    assert enriched["difficulty"] == "easy"

    with patch("intent.analyze.chat_json", return_value=None):
        analyzed = analyze_one(enriched, data_dir=None)
    assert len(analyzed["intents"]) == 2

    # Simulate reference success
    analyzed["fail_to_pass"] = ["tests/test_widgetmod.py::test_return"]
    analyzed["pass_to_pass"] = []
    spec = build_interactive_spec(analyzed)
    spec_path = tmp_path / f"{spec['task_id']}.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    loaded = json.loads(spec_path.read_text(encoding="utf-8"))

    episode = run_episode(loaded, make_stub_agent(max_agent_turns=3))
    scored = score_episode(loaded, episode, tests_passed=True)
    assert episode["stop_reason"] == "agent_done"
    speaking = sum(
        1
        for t in episode["sim_turns"]
        if t.get("action") in {"steer", "reveal_next", "answer", "turn0"}
    )
    assert episode["user_input_count"] == speaking
    assert scored["tests_passed"] is True
    assert scored["baseline_agent_runtime_sec"] == enriched["agent_runtime_sec"]
