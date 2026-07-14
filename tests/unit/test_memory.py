import pytest

import langbridge_code.memory as memory_mod
from langbridge_code.memory import (
    memory_index_text,
    parse_memory_extraction,
    prefetch_memory,
    read_memory_entry,
    read_memory_index,
    write_memory,
)


@pytest.fixture(autouse=True)
def _no_llm_prefetch():
    """Override the global stub — this module tests the real memory helpers."""
    yield


@pytest.fixture(autouse=True)
def temp_memory_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        memory_mod, "PROJECT_MEMORY_PATH", tmp_path / "repo" / ".langbridge" / "memory.md"
    )
    monkeypatch.setattr(memory_mod, "USER_MEMORY_PATH", tmp_path / "home" / "memory.md")


def _fake_llm(reply_text):
    def fake_response(api_key, model, messages, **kwargs):
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": reply_text}],
                }
            ]
        }

    return fake_response


def test_write_memory_creates_entry_file_and_index_line():
    result = write_memory("project", "Merge freeze", "3/5 起 merge 冻结。")
    assert result.startswith("Saved to project memory")
    assert "project/merge-freeze.md" in result
    assert "- merge-freeze.md: Merge freeze" in read_memory_index("project")
    assert "3/5 起 merge 冻结" in read_memory_entry("project", "merge-freeze.md")

    result = write_memory("user", "Reply style", "偏好简短回复。")
    assert result.startswith("Saved to user memory")
    assert "- reply-style.md: Reply style" in read_memory_index("user")


def test_write_memory_same_title_overwrites_entry():
    write_memory("project", "Test rule", "不许 mock 数据库。")
    write_memory("project", "Test rule", "改了:可以 mock 外部 API。")
    index = read_memory_index("project")
    assert index.count("test-rule.md") == 1
    body = read_memory_entry("project", "test-rule.md")
    assert "可以 mock 外部 API" in body
    assert "不许 mock 数据库" not in body


def test_write_memory_rejects_bad_scope_and_empty_content():
    assert "Unknown memory scope" in write_memory("global", "t", "x")
    assert "nothing saved" in write_memory("project", "title", "   ")
    assert "nothing saved" in write_memory("project", "  ", "content")


def test_memory_index_text_combines_scopes():
    assert memory_index_text() == ""
    write_memory("user", "Reply style", "简短")
    write_memory("project", "Build", "pytest -q")
    combined = memory_index_text()
    assert "## user" in combined
    assert "## project" in combined
    assert "reply-style.md" in combined
    assert "build.md" in combined


def test_prefetch_memory_reads_selected_files(monkeypatch):
    write_memory("user", "Reply style", "偏好简短回复。")
    write_memory("project", "Build", "用 pytest -q 跑测试。")
    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        _fake_llm("user/reply-style.md\nproject/build.md\nproject/missing.md"),
    )
    block = prefetch_memory("key", "model", "跑一下测试")
    assert "## user/reply-style.md" in block
    assert "偏好简短回复" in block
    assert "## project/build.md" in block
    assert "missing" not in block


def test_prefetch_memory_none_reply_and_empty_index(monkeypatch):
    assert prefetch_memory("key", "model", "task") == ""  # no index yet
    write_memory("project", "Build", "pytest")
    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response", _fake_llm("NONE")
    )
    assert prefetch_memory("key", "model", "task") == ""


def test_prefetch_memory_swallows_llm_failure(monkeypatch):
    write_memory("project", "Build", "pytest")

    def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("langbridge_code.llm.client.create_model_response", boom)
    assert prefetch_memory("key", "model", "task") == ""


def test_parse_memory_extraction_blocks_and_none():
    assert parse_memory_extraction("NONE") == []
    reply = (
        "MEMORY_SCOPE: user\n"
        "MEMORY_TITLE: Reply style\n"
        "MEMORY_CONTENT: 用户偏好中文简短回复。\n"
        "MEMORY_SCOPE: project\n"
        "MEMORY_TITLE: Commit style\n"
        "MEMORY_CONTENT: commit 信息用英文。\n"
    )
    parsed = parse_memory_extraction(reply)
    assert parsed == [
        ("user", "Reply style", "用户偏好中文简短回复。"),
        ("project", "Commit style", "commit 信息用英文。"),
    ]


def test_extract_and_write_memories_uses_live_context_fork(monkeypatch):
    captured = {}

    def fake_response(api_key, model, messages, **kwargs):
        captured["messages"] = messages
        return _fake_llm(
            "MEMORY_SCOPE: project\nMEMORY_TITLE: Async note\nMEMORY_CONTENT: 记住这个。"
        )(api_key, model, messages, **kwargs)

    monkeypatch.setattr("langbridge_code.llm.client.create_model_response", fake_response)
    live = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "done"}]
    results = memory_mod.extract_and_write_memories("key", "model", live)
    assert len(results) == 1
    assert "记住这个" in read_memory_entry("project", "async-note.md")
    # Fork = live messages + one instruction (prefix-cache friendly).
    assert captured["messages"][:2] == live
    assert captured["messages"][-1]["role"] == "user"
    assert "memory writer" in captured["messages"][-1]["content"].lower()


def test_schedule_memory_extraction_runs_in_background(monkeypatch):
    import time

    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        _fake_llm(
            "MEMORY_SCOPE: project\nMEMORY_TITLE: Async note\nMEMORY_CONTENT: async body"
        ),
    )
    memory_mod.schedule_memory_extraction("key", "model", [{"role": "user", "content": "hi"}])
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if "async body" in read_memory_entry("project", "async-note.md"):
            break
        time.sleep(0.02)
    assert "async body" in read_memory_entry("project", "async-note.md")
