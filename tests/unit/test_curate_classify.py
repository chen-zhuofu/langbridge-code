import json
import sys
from pathlib import Path

import pytest

_CURATE = Path(__file__).resolve().parents[2] / "eval" / "data-pipeline" / "curate"
if str(_CURATE) not in sys.path:
    sys.path.insert(0, str(_CURATE))

from classify import (  # noqa: E402
    _parse_classification,
    _user_payload,
    apply_classification,
    classify_instance,
)


def test_parse_classification_accepts_aliases():
    parsed = _parse_classification(
        "{"
        '"task_kind": "bug-fix", '
        '"difficulty": "HARD", '
        '"task_type_reason": "restores broken behavior", '
        '"difficulty_reason": "multi-file"'
        "}"
    )
    assert parsed == {
        "task_type": "bug_fix",
        "task_type_reason": "restores broken behavior",
        "difficulty": "hard",
        "difficulty_reason": "multi-file",
    }


def test_parse_classification_rejects_invalid_type():
    with pytest.raises(ValueError, match="task_type"):
        _parse_classification(
            '{"task_type": "docs", "difficulty": "easy",'
            ' "task_type_reason": "x", "difficulty_reason": "y"}'
        )


def test_parse_classification_accepts_unknown():
    parsed = _parse_classification(
        '{"task_type": "unkown", "difficulty": "unknown",'
        ' "task_type_reason": "mixed bugfix and feature signals",'
        ' "difficulty_reason": "scope unclear from one test"}'
    )
    assert parsed == {
        "task_type": "unknown",
        "task_type_reason": "mixed bugfix and feature signals",
        "difficulty": "unknown",
        "difficulty_reason": "scope unclear from one test",
    }


def test_apply_classification_sets_hard_flag():
    instance = {"instance_id": "x", "metadata": {"classify_reason": "old"}}
    apply_classification(
        instance,
        {
            "task_type": "feature",
            "difficulty": "hard",
            "task_type_reason": "adds new API",
            "difficulty_reason": "broad API",
        },
    )
    assert instance["task_type"] == "feature"
    assert instance["difficulty"] == "hard"
    assert instance["hard"] is True
    assert instance["task_type_reason"] == "adds new API"
    assert instance["difficulty_reason"] == "broad API"
    assert "classify_reason" not in instance["metadata"]
    assert "task_kind" not in instance


def test_apply_classification_unknown_when_missing():
    instance = {}
    apply_classification(instance, None)
    assert instance["task_type"] == "unknown"
    assert instance["difficulty"] == "unknown"
    assert instance["hard"] is False
    assert instance["task_type_reason"]
    assert instance["difficulty_reason"]


def test_instance_to_task_keeps_llm_difficulty():
    sys.path.insert(0, str(_CURATE.parent))
    from _lib.spec import instance_to_task

    task = instance_to_task(
        {
            "instance_id": "org__repo-1",
            "task_id": "org__repo-1",
            "repo": "org/repo",
            "base_commit": "abc",
            "problem_statement": "fix it",
            "patch": "",
            "test_patch": "",
            "FAIL_TO_PASS": ["t1", "t2", "t3"],
            "task_type": "bug_fix",
            "difficulty": "easy",
            "task_type_reason": "restores broken behavior",
            "difficulty_reason": "localized",
            "problem_statement_source": "rewritten",
            "rewrite_reason": "salvage noisy but usable statement",
        }
    )
    assert task["task_type"] == "bug_fix"
    assert task["difficulty"] == "easy"
    assert task["hard"] is False
    assert task["task_type_reason"] == "restores broken behavior"
    assert task["difficulty_reason"] == "localized"
    assert task["problem_statement_source"] == "rewritten"
    assert task["rewrite_reason"] == "salvage noisy but usable statement"


def test_user_payload_includes_f2p():
    payload = json.loads(
        _user_payload(
            {
                "task_id": "repo__1",
                "repo": "org/repo",
                "metadata": {"num_files": 3},
                "problem_statement": "Fix caching.",
                "fail_to_pass": [
                    "tests/test_a.py::test_one",
                    "tests/test_b.py::test_two",
                ],
                "pass_to_pass": ["tests/test_a.py::test_ok"],
            }
        )
    )
    assert payload["num_files"] == 3
    assert payload["fail_to_pass_count"] == 2
    assert payload["fail_to_pass_names"][0].endswith("test_one")
    assert payload["pass_to_pass_count"] == 1


def test_classify_instance_calls_model(monkeypatch):
    calls = {}

    def fake_response(api_key, model, messages, **kwargs):
        calls["label"] = kwargs.get("label")
        calls["messages"] = messages
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                '{"task_type":"refactor","difficulty":"easy",'
                                '"task_type_reason":"rename only",'
                                '"difficulty_reason":"one file"}'
                            ),
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response", fake_response
    )
    result = classify_instance(
        {
            "task_id": "repo__1",
            "repo": "org/repo",
            "metadata": {"num_files": 2},
            "problem_statement": "Rename helper without behavior change.",
            "fail_to_pass": ["tests/test_rename.py::test_alias"],
        },
        api_key="key",
        model="model",
    )
    assert result["task_type"] == "refactor"
    assert result["difficulty"] == "easy"
    assert result["task_type_reason"] == "rename only"
    assert result["difficulty_reason"] == "one file"
    assert calls["label"] == "curate-classify"
    user = calls["messages"][1]["content"]
    assert "Rename helper" in user
    assert "fail_to_pass_count" in user
    assert "test_alias" in user
