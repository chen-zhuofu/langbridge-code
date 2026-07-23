import time

import pytest

import langbridge_code.memory as memory_mod
from langbridge_code.memory import (
    memory_index_text,
    parse_memory_entry,
    prefetch_memory,
    read_memory_entry,
    read_memory_index,
    valid_scope_type,
    write_memory,
)


@pytest.fixture(autouse=True)
def _no_llm_prefetch():
    """Override the global offline stub; these tests exercise real memory helpers."""
    yield


@pytest.fixture(autouse=True)
def temp_memory_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        memory_mod, "PROJECT_MEMORY_PATH", tmp_path / "repo" / ".langbridge" / "memory.md"
    )
    monkeypatch.setattr(
        memory_mod, "USER_MEMORY_PATH", tmp_path / "home" / ".langbridge-code" / "memory.md"
    )


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


def test_scope_and_type_are_independent():
    assert valid_scope_type("user", "user")
    assert valid_scope_type("user", "feedback")
    assert valid_scope_type("user", "reference")
    assert not valid_scope_type("user", "project")
    assert valid_scope_type("project", "user")
    assert valid_scope_type("project", "feedback")
    assert valid_scope_type("project", "reference")
    assert valid_scope_type("project", "project")


def test_write_memory_creates_frontmatter_entry_and_typed_index():
    result = write_memory(
        "project",
        "auth-rewrite-motivation",
        "认证重写是合规要求",
        "旧中间件必须替换。\n**Why:** 法务要求。\n**How to apply:** 优先合规。",
    )
    assert result.startswith("Saved project memory")
    raw = read_memory_entry("project", "auth-rewrite-motivation.md")
    assert raw.startswith("---\n")
    parsed = parse_memory_entry("project", "auth-rewrite-motivation.md", raw)
    assert parsed.name == "auth-rewrite-motivation"
    assert parsed.description == "认证重写是合规要求"
    assert parsed.memory_type == "project"
    assert "**Why:** 法务要求" in parsed.content
    assert (
        "- [project] auth-rewrite-motivation.md: 认证重写是合规要求"
        in read_memory_index("project")
    )


def test_user_and_feedback_share_user_memory_md():
    write_memory("user", "user-background", "用户熟悉后端", "用户有多年后端经验。")
    write_memory(
        "feedback",
        "response-style",
        "用户要求中文短句",
        "回答使用中文短句。\n**How to apply:** 先查代码再回答。",
    )
    index = read_memory_index("user")
    assert "[user] user-background.md" in index
    assert "[feedback] response-style.md" in index
    assert memory_mod.USER_MEMORY_PATH.is_file()


def test_project_and_reference_share_project_memory_md():
    write_memory("project", "compliance", "认证项目合规背景", "这是法务要求。")
    write_memory(
        "reference",
        "pipeline-tracker",
        "数据管道问题追踪位置",
        "去 Linear 的 INGEST 项目查找。",
    )
    index = read_memory_index("project")
    assert "[project] compliance.md" in index
    assert "[reference] pipeline-tracker.md" in index
    assert memory_mod.PROJECT_MEMORY_PATH.is_file()


def test_explicit_scope_supports_project_feedback_and_user_reference():
    write_memory(
        "feedback",
        "project-review-rule",
        "当前项目先跑集成测试",
        "在这个项目中提交前运行集成测试。",
        scope="project",
    )
    write_memory(
        "reference",
        "personal-style-guide",
        "用户的通用写作规范",
        "写作规范位于个人文档目录。",
        scope="user",
    )

    assert "[feedback] project-review-rule.md" in read_memory_index("project")
    assert "[reference] personal-style-guide.md" in read_memory_index("user")
    assert "not valid" in write_memory(
        "project",
        "bad",
        "错误组合",
        "不应写入。",
        scope="user",
    )


def test_same_name_overwrites_instead_of_duplicating():
    write_memory("feedback", "test-rule", "测试规则", "不许 mock 数据库。")
    result = write_memory(
        "feedback",
        "test-rule",
        "更新后的测试规则",
        "可以 mock 外部 API。",
    )
    assert result.startswith("Updated feedback memory")
    index = read_memory_index("user")
    assert index.count("test-rule.md") == 1
    body = read_memory_entry("user", "test-rule.md")
    assert "可以 mock 外部 API" in body
    assert "不许 mock 数据库" not in body


def test_semantic_dedupe_replaces_conflicting_topic_via_llm(monkeypatch):
    write_memory(
        "feedback",
        "local-app-form",
        "用户接受浏览器中的本地应用",
        "本地服务器自动打开浏览器即可。",
    )
    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        _fake_llm("local-app-form.md"),
    )
    result = write_memory(
        "feedback",
        "native-mac-app",
        "用户要求原生 Mac App",
        "必须是独立窗口、Dock 图标、双击打开的 .app，不是浏览器页面。",
        api_key="key",
        model="model",
    )
    assert result.startswith("Updated feedback memory")
    index = read_memory_index("user")
    assert index.count(".md") == 1
    body = read_memory_entry("user", "local-app-form.md")
    assert "不是浏览器页面" in body
    assert "自动打开浏览器即可" not in body


def test_write_memory_rejects_bad_type_and_empty_fields():
    assert "Unknown memory type" in write_memory("global", "n", "d", "x")
    assert "nothing saved" in write_memory("project", "name", "", "content")
    assert "nothing saved" in write_memory("project", "", "desc", "content")


def test_memory_index_text_reads_both_memory_md_files():
    assert memory_index_text() == ""
    write_memory("feedback", "reply-style", "简短回复", "使用短句。")
    write_memory("project", "motivation", "项目动机", "这是合规要求。")
    combined = memory_index_text()
    assert "## user" in combined
    assert "## project" in combined
    assert "reply-style.md" in combined
    assert "motivation.md" in combined


def test_prefetch_memory_reads_selected_files_from_both_indexes(monkeypatch):
    write_memory("feedback", "reply-style", "简短回复", "偏好简短回复。")
    write_memory("project", "motivation", "项目动机", "这是合规要求。")
    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response",
        _fake_llm("user/reply-style.md\nproject/motivation.md\nproject/missing.md"),
    )
    block = prefetch_memory("key", "model", "解释项目")
    assert "## user/reply-style.md" in block
    assert "偏好简短回复" in block
    assert "## project/motivation.md" in block
    assert "这是合规要求" in block
    assert "missing" not in block


def test_prefetch_memory_none_and_failure(monkeypatch):
    assert prefetch_memory("key", "model", "task") == ""
    write_memory("project", "motivation", "项目动机", "合规。")
    monkeypatch.setattr(
        "langbridge_code.llm.client.create_model_response", _fake_llm("NONE")
    )
    assert prefetch_memory("key", "model", "task") == ""

    def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("langbridge_code.llm.client.create_model_response", boom)
    assert prefetch_memory("key", "model", "task") == ""


def test_memory_writer_fork_uses_common_file_tools_for_add_update_delete(monkeypatch):
    write_memory(
        "feedback",
        "stale-app-form",
        "用户接受浏览器本地应用",
        "打开浏览器即可。",
    )
    live = [{"role": "user", "content": "我需要原生 Mac app，不要浏览器。"}]
    rounds = []

    def fake_response(api_key, model, messages, **kwargs):
        rounds.append((list(messages), kwargs))
        if len(rounds) == 1:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "read_file",
                        "call_id": "read-user-index",
                        "arguments": '{"purpose":"inspect index","path":"user/memory.md"}',
                    },
                    {
                        "type": "function_call",
                        "name": "read_file",
                        "call_id": "read-project-index",
                        "arguments": '{"purpose":"inspect index","path":"project/memory.md"}',
                    },
                ]
            }
        if len(rounds) == 2:
            assert "stale-app-form.md" in str(messages)
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "read_file",
                        "call_id": "read-stale",
                        "arguments": (
                            '{"purpose":"verify stale entry",'
                            '"path":"user/memory/stale-app-form.md"}'
                        ),
                    }
                ]
            }
        if len(rounds) == 3:
            return {
                "output": [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "delete-stale",
                        "arguments": (
                            '{"purpose":"remove inaccurate memory",'
                            '"command":"rm user/memory/stale-app-form.md"}'
                        ),
                    },
                    {
                        "type": "function_call",
                        "name": "write",
                        "call_id": "write-project-feedback",
                        "arguments": (
                            '{"purpose":"save project preference",'
                            '"path":"project/memory/native-app-form.md",'
                            '"content":"---\\nname: \\"native-app-form\\"\\n'
                            'description: \\"当前项目要求原生 Mac App\\"\\n'
                            'type: feedback\\n---\\n'
                            '必须提供独立窗口和 .app，不能使用浏览器页面。\\n"}'
                        ),
                    },
                ]
            }
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Memory reconciled."}],
                }
            ]
        }

    monkeypatch.setattr("langbridge_code.llm.client.create_model_response", fake_response)
    report = memory_mod.run_memory_writer_agent("key", "model", live)

    assert report == "Memory reconciled."
    assert read_memory_entry("user", "stale-app-form.md") == ""
    entry = parse_memory_entry(
        "project",
        "native-app-form.md",
        read_memory_entry("project", "native-app-form.md"),
    )
    assert entry.memory_type == "feedback"
    assert "不能使用浏览器" in entry.content
    assert "native-app-form.md" in read_memory_index("project")
    assert rounds[0][0][:1] == live
    tool_names = {schema["name"] for schema in rounds[0][1]["tool_schemas"]}
    assert {"read_file", "write", "Edit", "bash"} <= tool_names


def test_schedule_memory_writer_runs_tool_agent_in_background(monkeypatch):
    monkeypatch.setattr(
        memory_mod,
        "run_memory_writer_agent",
        lambda *args, **kwargs: write_memory(
            "feedback",
            "response-style",
            "回复风格",
            "使用中文短句。",
        ),
    )
    memory_mod.schedule_memory_writer(
        "key", "model", [{"role": "user", "content": "hi"}]
    )
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if "中文短句" in read_memory_entry("user", "response-style.md"):
            break
        time.sleep(0.02)
    assert "中文短句" in read_memory_entry("user", "response-style.md")


def test_legacy_entry_remains_readable():
    parsed = parse_memory_entry(
        "user",
        "old.md",
        "# 旧偏好\n\n用户偏好简短回复。\n",
    )
    assert parsed.name == "旧偏好"
    assert parsed.memory_type == "user"
    assert parsed.content == "用户偏好简短回复。"
