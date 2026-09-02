import json
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

import langbridge_code.mcp_runtime as mcp_module
from langbridge_code.gmail_api import GMAIL_API_BASE_URL
from langbridge_code.mcp_runtime import (
    CLIENT_TOOL_SEARCH_SCHEMA,
    GMAIL_ENDPOINT,
    KeychainCredentialStore,
    OfficialMCPClient,
    RemoteMCPRegistry,
    TOOL_SEARCH_SCHEMA,
    format_tool_pool_reminder,
)


def test_keychain_helper_round_trips_long_json_over_stdin(tmp_path):
    helper = tmp_path / "credential-helper"
    storage = tmp_path / "credential.json"
    helper.write_text(
        """#!/usr/bin/env python3
import pathlib, sys
storage = pathlib.Path(sys.argv[0]).with_name('credential.json')
operation = sys.argv[1]
if operation == 'set':
    storage.write_bytes(sys.stdin.buffer.read())
elif operation == 'get':
    if not storage.exists():
        raise SystemExit(44)
    sys.stdout.buffer.write(storage.read_bytes())
elif operation == 'delete':
    storage.unlink(missing_ok=True)
else:
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    credentials = KeychainCredentialStore(helper_path=helper)
    value = {
        "access_token": "a" * 1_024,
        "refresh_token": "r" * 1_024,
        "expires_at": 99_999_999_999,
    }

    credentials.set("gmail", value)

    assert storage.stat().st_size > 2_048
    assert credentials.get("gmail") == value
    credentials.delete("gmail")
    assert not storage.exists()


def test_official_mcp_client_initializes_sdk_sessions(monkeypatch):
    events = []

    @asynccontextmanager
    async def fake_transport(url, *, http_client):
        events.append(("transport", url, http_client.headers["authorization"]))
        yield object(), object(), lambda: None

    class FakeSession:
        def __init__(self, _read_stream, _write_stream):
            pass

        async def __aenter__(self):
            events.append(("enter",))
            return self

        async def __aexit__(self, *_args):
            events.append(("exit",))

        async def initialize(self):
            events.append(("initialize",))

        async def list_tools(self):
            return ListToolsResult(
                tools=[
                    Tool(
                        name="search_threads",
                        inputSchema={"type": "object", "properties": {}},
                    )
                ]
            )

        async def call_tool(self, name, arguments):
            events.append(("call", name, arguments))
            return CallToolResult(
                content=[TextContent(type="text", text="two matching threads")],
                isError=False,
            )

    monkeypatch.setattr(mcp_module, "streamable_http_client", fake_transport)
    monkeypatch.setattr(mcp_module, "ClientSession", FakeSession)
    client = OfficialMCPClient()

    tools = client.list_tools(GMAIL_ENDPOINT, "test-token")
    result = client.call_tool(
        GMAIL_ENDPOINT,
        "test-token",
        "search_threads",
        {"query": "newer_than:1d"},
    )

    assert tools["tools"][0]["name"] == "search_threads"
    assert result["content"][0]["text"] == "two matching threads"
    assert events.count(("initialize",)) == 2
    assert ("transport", GMAIL_ENDPOINT, "Bearer test-token") in events
    assert ("call", "search_threads", {"query": "newer_than:1d"}) in events


def test_official_mcp_client_preserves_google_error_tool_result(monkeypatch):
    response = httpx.Response(
        403,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "Enable the Gmail MCP API."}],
                "isError": True,
            },
        },
        request=httpx.Request("POST", GMAIL_ENDPOINT),
    )
    http_error = httpx.HTTPStatusError(
        "403 Forbidden",
        request=response.request,
        response=response,
    )

    def fail_run(*_args):
        raise ExceptionGroup("MCP transport failed", [http_error])

    monkeypatch.setattr(mcp_module.anyio, "run", fail_run)

    result = OfficialMCPClient().call_tool(
        GMAIL_ENDPOINT,
        "test-token",
        "search_threads",
        {},
    )

    assert result["isError"] is True
    assert result["content"][0]["text"] == "Enable the Gmail MCP API."


class FakeCredentials:
    def __init__(self):
        self.values = {
            "gmail": {"access_token": "test-token", "expires_at": 99999999999}
        }

    def get(self, account):
        value = self.values.get(account)
        return dict(value) if value else None

    def set(self, account, value):
        self.values[account] = dict(value)


class FakeMCPClient:
    def __init__(self, calls):
        self.calls = calls

    def list_tools(self, url, access_token):
        self.calls.append(
            {"method": "tools/list", "url": url, "access_token": access_token}
        )
        return {
            "tools": [
                {
                    "name": "search_threads",
                    "description": "Search Gmail threads without modifying mail.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
                {
                    "name": "create_draft",
                    "description": "Write a draft.",
                    "inputSchema": {"type": "object"},
                },
            ]
        }

    def call_tool(self, url, access_token, name, arguments):
        self.calls.append(
            {
                "method": "tools/call",
                "url": url,
                "access_token": access_token,
                "params": {"name": name, "arguments": arguments},
            }
        )
        return {"content": [{"type": "text", "text": "two matching threads"}]}


@pytest.fixture
def configured_mcp(tmp_path, monkeypatch):
    support = tmp_path / "support"
    support.mkdir()
    monkeypatch.setenv("LANGBRIDGE_APP_SUPPORT_DIR", str(support))
    (support / "mcp.json").write_text(
        json.dumps(
            {
                "version": 1,
                "servers": {
                    "gmail": {
                        "enabled": True,
                        "transport": "streamable-http",
                        "url": "https://mcp.example.com/v1",
                        "agents": ["langbridge"],
                        "credential_account": "gmail",
                        "allowed_tools": [
                            "search_threads",
                            "get_message",
                            "get_thread",
                            "list_labels",
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls = []
    return support, FakeMCPClient(calls), calls


@pytest.fixture
def configured_gmail_api(tmp_path, monkeypatch):
    support = tmp_path / "support"
    support.mkdir()
    monkeypatch.setenv("LANGBRIDGE_APP_SUPPORT_DIR", str(support))
    (support / "mcp.json").write_text(
        json.dumps(
            {
                "version": 1,
                "servers": {
                    "gmail": {
                        "enabled": True,
                        "transport": "gmail-api",
                        "agents": ["langbridge"],
                        "credential_account": "gmail",
                        "allowed_tools": [
                            "search_threads",
                            "get_message",
                            "get_thread",
                            "list_labels",
                            "send_message",
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls = []
    return support, FakeMCPClient(calls), calls


def test_mcp_tools_are_names_only_until_exact_tool_search(configured_mcp):
    _, client, calls = configured_mcp
    registry = RemoteMCPRegistry(
        credential_store=FakeCredentials(),
        mcp_client=client,
    )

    assert registry.available_tool_names() == (
        "gmail.get_message",
        "gmail.get_thread",
        "gmail.list_labels",
        "gmail.search_threads",
    )
    assert registry.loaded_schemas() == []

    selected = registry.search("select:gmail.search_threads")
    assert selected["selected"] == "gmail.search_threads"
    assert selected["call_name"] == "mcp__gmail__search_threads"
    assert selected["tools"][0]["parameters"]["required"] == ["query"]
    assert selected["mcp_schema"]["name"] == "search_threads"
    assert registry.loaded_schemas() == selected["tools"]
    assert calls[0]["method"] == "tools/list"


def test_mcp_read_only_allowlist_blocks_write_tools(configured_mcp):
    _, client, _ = configured_mcp
    registry = RemoteMCPRegistry(credential_store=FakeCredentials(), mcp_client=client)

    with pytest.raises(PermissionError, match="not allowlisted"):
        registry.search("select:gmail.create_draft")


def test_loaded_mcp_tool_executes_then_denies_stale_schema(configured_mcp):
    support, client, calls = configured_mcp
    registry = RemoteMCPRegistry(credential_store=FakeCredentials(), mcp_client=client)
    selected = registry.search("select:gmail.search_threads")

    output = registry.call(selected["call_name"], {"query": "newer_than:1d"})
    assert "two matching threads" in output
    assert calls[-1]["method"] == "tools/call"
    assert calls[-1]["params"] == {
        "name": "search_threads",
        "arguments": {"query": "newer_than:1d"},
    }

    config = json.loads((support / "mcp.json").read_text(encoding="utf-8"))
    config["servers"]["gmail"]["enabled"] = False
    (support / "mcp.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(PermissionError, match="not available"):
        registry.call(selected["call_name"], {"query": "anything"})


def test_registry_is_agent_and_schedule_scoped(configured_mcp):
    _, client, _ = configured_mcp
    credentials = FakeCredentials()
    assert RemoteMCPRegistry(
        role="worker", credential_store=credentials, mcp_client=client
    ).available_tool_names() == ()
    assert RemoteMCPRegistry(
        allowed_servers=set(), credential_store=credentials, mcp_client=client
    ).available_tool_names() == ()
    assert RemoteMCPRegistry(
        allowed_servers={"gmail"}, credential_store=credentials, mcp_client=client
    ).available_tool_names()


def test_tool_search_is_fixed_discovery_and_reminder_contains_names_only():
    assert TOOL_SEARCH_SCHEMA["name"] == "ToolSearch"
    assert CLIENT_TOOL_SEARCH_SCHEMA["type"] == "tool_search"
    assert CLIENT_TOOL_SEARCH_SCHEMA["execution"] == "client"
    reminder = format_tool_pool_reminder(("gmail.search_threads",))
    assert "gmail.search_threads" in reminder
    assert "parameters" not in reminder
    delta = format_tool_pool_reminder(
        ("gmail.get_thread",), ("gmail.search_threads",)
    )
    assert "Added: gmail.get_thread" in delta
    assert "Removed: gmail.search_threads" in delta


def test_native_search_tools_are_deferred_function_schemas(configured_mcp):
    _, client, _ = configured_mcp
    registry = RemoteMCPRegistry(credential_store=FakeCredentials(), mcp_client=client)
    selected = registry.search("select:gmail.search_threads")

    tools = registry.native_search_tools(selected)

    assert tools[0]["type"] == "function"
    assert tools[0]["name"] == "mcp__gmail__search_threads"
    assert tools[0]["defer_loading"] is True


def test_local_gmail_api_uses_fixed_rest_host_and_hard_read_only_allowlist(
    configured_gmail_api,
):
    _, gmail_client, calls = configured_gmail_api
    registry = RemoteMCPRegistry(
        credential_store=FakeCredentials(),
        gmail_client=gmail_client,
    )

    assert "gmail.send_message" not in registry.available_tool_names()
    with pytest.raises(PermissionError, match="not allowlisted"):
        registry.search("select:gmail.send_message")

    selected = registry.search("select:gmail.search_threads")
    output = registry.call(selected["call_name"], {"query": "newer_than:1d"})

    assert "two matching threads" in output
    assert {call["url"] for call in calls} == {GMAIL_API_BASE_URL}
    assert [call["method"] for call in calls] == ["tools/list", "tools/call"]


def test_legacy_google_mcp_config_routes_locally_without_relogin(configured_gmail_api):
    support, gmail_client, calls = configured_gmail_api
    config = json.loads((support / "mcp.json").read_text(encoding="utf-8"))
    config["servers"]["gmail"].update(
        {"transport": "streamable-http", "url": GMAIL_ENDPOINT}
    )
    (support / "mcp.json").write_text(json.dumps(config), encoding="utf-8")
    registry = RemoteMCPRegistry(
        credential_store=FakeCredentials(),
        gmail_client=gmail_client,
    )

    selected = registry.search("select:gmail.search_threads")
    registry.call(selected["call_name"], {"query": "anything"})

    assert {call["url"] for call in calls} == {GMAIL_API_BASE_URL}


def test_local_gmail_api_rejects_a_credential_with_broader_scopes(configured_gmail_api):
    _, gmail_client, calls = configured_gmail_api
    credentials = FakeCredentials()
    credentials.values["gmail"]["scope"] = (
        "https://www.googleapis.com/auth/gmail.modify"
    )
    registry = RemoteMCPRegistry(
        credential_store=credentials,
        gmail_client=gmail_client,
    )

    with pytest.raises(PermissionError, match="not limited to gmail.readonly"):
        registry.search("select:gmail.search_threads")
    assert calls == []
