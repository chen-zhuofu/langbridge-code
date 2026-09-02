"""MCP runtime support with agent-scoped, deferred tool discovery.

The model initially sees only :data:`TOOL_SEARCH_SCHEMA`.  MCP tool names are
announced separately, and a selected tool's full schema is added only after a
``ToolSearch(query="select:<server>.<tool>")`` call.

Official streamable-HTTP MCP servers and LangBridge's local, read-only Gmail API
adapter share this deferred agent-facing protocol.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from langbridge_code.gmail_api import GMAIL_API_BASE_URL, GmailAPIClient
from langbridge_code.util.app_paths import app_support_dir

MCP_CONFIG_VERSION = 1
GMAIL_SERVER_ID = "gmail"
GMAIL_ENDPOINT = "https://gmailmcp.googleapis.com/mcp/v1"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_API_TRANSPORT = "gmail-api"
GMAIL_READ_ONLY_TOOLS = (
    "search_threads",
    "get_message",
    "get_thread",
    "list_labels",
)
DEFAULT_KEYCHAIN_SERVICE = "com.langbridge.app.mcp"

TOOL_SEARCH_SCHEMA = {
    "type": "function",
    "name": "ToolSearch",
    "description": (
        "Load the full schema for one deferred tool whose name was listed in a "
        "<system-reminder>. Use the exact form select:<server>.<tool>."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Exact deferred selection, for example select:gmail.search_threads.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

# OpenAI GPT-5.4+ can ask the client to discover deferred tools without
# pretending discovery is a normal function call.  Keep the ordinary function
# schema above for chat-completions providers, which do not implement these
# Responses API item types.
CLIENT_TOOL_SEARCH_SCHEMA = {
    "type": "tool_search",
    "execution": "client",
    "description": (
        "Load the full schema for one deferred tool whose name was listed in a "
        "<system-reminder>. Use the exact form select:<server>.<tool>."
    ),
    "parameters": TOOL_SEARCH_SCHEMA["parameters"],
}


def mcp_config_path() -> Path:
    return app_support_dir() / "mcp.json"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_mcp_config() -> dict:
    value = _read_json(mcp_config_path())
    return value if value.get("version") == MCP_CONFIG_VERSION else {}


class KeychainCredentialStore:
    """Read and update opaque JSON credentials in the user's macOS Keychain."""

    def __init__(
        self,
        service: str = DEFAULT_KEYCHAIN_SERVICE,
        helper_path: str | Path | None = None,
    ):
        self.service = service
        configured = helper_path or os.environ.get("LANGBRIDGE_CREDENTIAL_HELPER", "")
        self.helper_path = Path(configured).expanduser() if configured else None

    def _run(self, operation: str, account: str, *, input_data: bytes | None = None):
        if self.helper_path is None or not self.helper_path.is_file():
            raise RuntimeError("LangBridge credential helper is unavailable.")
        return subprocess.run(
            [str(self.helper_path), operation, self.service, account],
            input=input_data,
            capture_output=True,
            check=False,
        )

    def get(self, account: str) -> dict | None:
        try:
            completed = self._run("get", account)
        except RuntimeError:
            return None
        if completed.returncode != 0:
            return None
        try:
            value = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def set(self, account: str, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        completed = self._run("set", account, input_data=payload)
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "Could not update macOS Keychain.")

    def delete(self, account: str) -> None:
        completed = self._run("delete", account)
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "Could not remove the macOS Keychain credential.")


@dataclass(frozen=True)
class DeferredMCPTool:
    public_name: str
    call_name: str
    server_id: str
    remote_name: str
    schema: dict


def _server_call_name(server_id: str, tool_name: str) -> str:
    """Return a provider-safe function name for a namespaced MCP tool."""
    clean_server = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in server_id)
    clean_tool = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in tool_name)
    return f"mcp__{clean_server}__{clean_tool}"[:64]


class OfficialMCPClient:
    """Synchronous facade over the official asynchronous MCP Python SDK."""

    def list_tools(self, url: str, access_token: str) -> dict:
        return self._run(self._list_tools, url, access_token)

    def call_tool(
        self, url: str, access_token: str, name: str, arguments: dict
    ) -> dict:
        return self._run(self._call_tool, url, access_token, name, arguments)

    @staticmethod
    def _run(operation, *args) -> dict:
        try:
            return anyio.run(operation, *args)
        except Exception as error:
            # Google currently sometimes returns a valid JSON-RPC tool result
            # with HTTP 403.  The SDK correctly rejects the non-2xx status, but
            # preserving that structured result gives the agent the actionable
            # server message instead of an opaque task-group error.
            result = _json_rpc_result_from_error(error)
            if result is not None:
                return result
            raise

    @staticmethod
    async def _list_tools(url: str, access_token: str) -> dict:
        async with OfficialMCPClient._session(url, access_token) as session:
            result = await session.list_tools()
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    @staticmethod
    async def _call_tool(url: str, access_token: str, name: str, arguments: dict) -> dict:
        async with OfficialMCPClient._session(url, access_token) as session:
            result = await session.call_tool(name, arguments)
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)

    @staticmethod
    def _session(url: str, access_token: str):
        return _OfficialMCPSession(url, access_token)


class _OfficialMCPSession:
    """Own one initialized streamable-HTTP SDK session."""

    def __init__(self, url: str, access_token: str):
        self.url = url
        self.access_token = access_token
        self._stack = AsyncExitStack()

    async def __aenter__(self):
        http_client = await self._stack.enter_async_context(
            httpx.AsyncClient(
                headers={"authorization": f"Bearer {self.access_token}"},
                timeout=30.0,
                follow_redirects=True,
            )
        )
        try:
            read_stream, write_stream, _ = await self._stack.enter_async_context(
                streamable_http_client(self.url, http_client=http_client)
            )
            session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            return session
        except BaseException:
            await self._stack.aclose()
            raise

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._stack.__aexit__(exc_type, exc, traceback)


def _json_rpc_result_from_error(error: BaseException) -> dict | None:
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            result = _json_rpc_result_from_error(nested)
            if result is not None:
                return result
        return None
    if not isinstance(error, httpx.HTTPStatusError):
        return None
    try:
        value = error.response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        return None
    result = value.get("result")
    return result if isinstance(result, dict) else None


class RemoteMCPRegistry:
    """Configured deferred tool servers and dynamically loaded tool schemas."""

    def __init__(
        self,
        *,
        role: str = "langbridge",
        allowed_servers: set[str] | frozenset[str] | None = None,
        credential_store=None,
        client: httpx.Client | None = None,
        mcp_client=None,
        gmail_client=None,
    ):
        self.role = role
        self.allowed_servers = None if allowed_servers is None else frozenset(allowed_servers)
        self.credentials = credential_store or KeychainCredentialStore()
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        self.mcp_client = mcp_client or OfficialMCPClient()
        self.gmail_client = gmail_client or GmailAPIClient(self.client)
        self._loaded: dict[str, DeferredMCPTool] = {}

    def _servers(self) -> dict[str, dict]:
        config = load_mcp_config()
        servers = config.get("servers") or {}
        return servers if isinstance(servers, dict) else {}

    def _server_is_allowed(self, server_id: str, server: dict) -> bool:
        if not server.get("enabled", False):
            return False
        transport = self._effective_transport(server_id, server)
        if transport not in {"streamable-http", GMAIL_API_TRANSPORT}:
            return False
        if transport == GMAIL_API_TRANSPORT and server_id != GMAIL_SERVER_ID:
            return False
        roles = server.get("agents") or ["langbridge"]
        if self.role not in roles:
            return False
        if self.allowed_servers is not None and server_id not in self.allowed_servers:
            return False
        account = str(server.get("credential_account") or server_id)
        return self.credentials.get(account) is not None

    @staticmethod
    def _effective_transport(server_id: str, server: dict) -> str:
        transport = str(server.get("transport") or "streamable-http")
        # Existing installs used Google's Developer Preview MCP endpoint.  Its
        # OAuth token already has gmail.readonly, so route that exact legacy
        # configuration through the local REST adapter without another login.
        if (
            server_id == GMAIL_SERVER_ID
            and transport == "streamable-http"
            and str(server.get("url") or "").rstrip("/") == GMAIL_ENDPOINT
        ):
            return GMAIL_API_TRANSPORT
        return transport

    def _allowed_tool_names(self, server_id: str, server: dict) -> tuple[str, ...]:
        configured = server.get("allowed_tools") or []
        if self._effective_transport(server_id, server) == GMAIL_API_TRANSPORT:
            return tuple(
                name
                for name in configured
                if isinstance(name, str) and name in GMAIL_READ_ONLY_TOOLS
            )
        return tuple(name for name in configured if isinstance(name, str) and name.strip())

    def available_tool_names(self) -> tuple[str, ...]:
        names = []
        for server_id, server in sorted(self._servers().items()):
            if not isinstance(server, dict) or not self._server_is_allowed(server_id, server):
                continue
            allowed_tools = self._allowed_tool_names(server_id, server)
            names.extend(
                f"{server_id}.{name}"
                for name in allowed_tools
                if isinstance(name, str) and name.strip()
            )
        return tuple(sorted(set(names)))

    def _resolve_public_name(self, public_name: str) -> tuple[str, dict, str]:
        server_id, separator, remote_name = public_name.partition(".")
        if not separator or not server_id or not remote_name:
            raise ValueError("Use an exact deferred tool name such as gmail.search_threads.")
        server = self._servers().get(server_id)
        if not isinstance(server, dict) or not self._server_is_allowed(server_id, server):
            raise PermissionError(f"Deferred tool is not available to {self.role}: {public_name}")
        if remote_name not in self._allowed_tool_names(server_id, server):
            raise PermissionError(f"Deferred tool is not allowlisted: {public_name}")
        return server_id, server, remote_name

    def _access_token(self, server_id: str, server: dict) -> str:
        account = str(server.get("credential_account") or server_id)
        credential = self.credentials.get(account)
        if not credential:
            raise PermissionError(f"{server_id} is not connected.")
        if self._effective_transport(server_id, server) == GMAIL_API_TRANSPORT:
            claimed_scope = str(
                credential.get("scope") or credential.get("scope_requested") or ""
            )
            if claimed_scope and set(claimed_scope.split()) != {GMAIL_SCOPE}:
                raise PermissionError("Gmail credential is not limited to gmail.readonly.")
        expires_at = float(credential.get("expires_at") or 0)
        if expires_at and expires_at <= time.time() + 60:
            credential = self._refresh_oauth_token(server_id, server, account, credential)
        token = str(credential.get("access_token") or "").strip()
        if not token:
            raise PermissionError(f"{server_id} credential has no access token.")
        return token

    def _refresh_oauth_token(
        self, server_id: str, server: dict, account: str, credential: dict
    ) -> dict:
        refresh_token = str(credential.get("refresh_token") or "").strip()
        oauth = server.get("oauth") or {}
        client_id = str(oauth.get("client_id") or "").strip()
        if not refresh_token or not client_id:
            raise PermissionError(f"{server_id} login expired; reconnect it in Settings.")
        form = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        client_secret = str(oauth.get("client_secret") or "").strip()
        if client_secret:
            form["client_secret"] = client_secret
        token_uri = str(oauth.get("token_uri") or "https://oauth2.googleapis.com/token")
        if (
            self._effective_transport(server_id, server) == GMAIL_API_TRANSPORT
            and token_uri != "https://oauth2.googleapis.com/token"
        ):
            raise PermissionError("Gmail token refresh is restricted to Google's token endpoint.")
        response = self.client.post(token_uri, data=form)
        response.raise_for_status()
        refreshed = response.json()
        if not isinstance(refreshed, dict) or not refreshed.get("access_token"):
            raise RuntimeError(f"{server_id} token refresh returned an invalid response.")
        if self._effective_transport(server_id, server) == GMAIL_API_TRANSPORT:
            granted = str(refreshed.get("scope") or credential.get("scope") or "").split()
            if granted and set(granted) != {GMAIL_SCOPE}:
                raise PermissionError("Gmail granted scopes are not exactly gmail.readonly.")
        updated = dict(credential)
        updated.update(refreshed)
        updated["refresh_token"] = refreshed.get("refresh_token") or refresh_token
        updated["expires_at"] = time.time() + float(refreshed.get("expires_in") or 3600)
        self.credentials.set(account, updated)
        return updated

    def _server_url(self, server_id: str, server: dict) -> str:
        if self._effective_transport(server_id, server) == GMAIL_API_TRANSPORT:
            return GMAIL_API_BASE_URL
        url = str(server.get("url") or "").strip()
        if not url.startswith("https://"):
            raise ValueError(f"Remote MCP server {server_id!r} must use HTTPS.")
        return url

    def _tool_client(self, server_id: str, server: dict):
        if self._effective_transport(server_id, server) == GMAIL_API_TRANSPORT:
            return self.gmail_client
        return self.mcp_client

    def search(self, query: str) -> dict:
        raw = str(query or "").strip()
        if not raw.startswith("select:"):
            raise ValueError("ToolSearch only accepts exact selections: select:<server>.<tool>.")
        public_name = raw[len("select:") :].strip()
        server_id, server, remote_name = self._resolve_public_name(public_name)
        result = self._tool_client(server_id, server).list_tools(
            self._server_url(server_id, server),
            self._access_token(server_id, server),
        )
        tools = result.get("tools") or []
        remote = next(
            (
                value
                for value in tools
                if isinstance(value, dict) and value.get("name") == remote_name
            ),
            None,
        )
        if remote is None:
            raise RuntimeError(f"MCP server did not publish the selected tool: {public_name}")
        call_name = _server_call_name(server_id, remote_name)
        schema = {
            "type": "function",
            "name": call_name,
            "description": str(remote.get("description") or f"MCP tool {public_name}"),
            "parameters": remote.get("inputSchema")
            if isinstance(remote.get("inputSchema"), dict)
            else {"type": "object", "properties": {}},
        }
        loaded = DeferredMCPTool(
            public_name=public_name,
            call_name=call_name,
            server_id=server_id,
            remote_name=remote_name,
            schema=schema,
        )
        self._loaded[call_name] = loaded
        return {
            "selected": public_name,
            "call_name": call_name,
            "tools": [schema],
            "mcp_schema": remote,
        }

    @staticmethod
    def native_search_tools(selection: dict) -> list[dict]:
        """Return Responses API deferred schemas for ``tool_search_output``."""
        return [
            {**schema, "defer_loading": True}
            for schema in selection.get("tools") or []
            if isinstance(schema, dict) and schema.get("type") == "function"
        ]

    def loaded_schemas(self) -> list[dict]:
        return [value.schema for _, value in sorted(self._loaded.items())]

    def is_loaded_call(self, call_name: str) -> bool:
        return call_name in self._loaded

    def call(self, call_name: str, arguments: dict) -> str:
        loaded = self._loaded.get(call_name)
        if loaded is None:
            raise ValueError(f"MCP tool schema has not been loaded: {call_name}")
        # Re-resolve on every execution.  A disconnect/config change therefore
        # denies stale schemas that may still be present in conversation history.
        server_id, server, remote_name = self._resolve_public_name(loaded.public_name)
        if remote_name != loaded.remote_name:
            raise PermissionError(f"Deferred tool is no longer available: {loaded.public_name}")
        result = self._tool_client(server_id, server).call_tool(
            self._server_url(server_id, server),
            self._access_token(server_id, server),
            remote_name,
            dict(arguments or {}),
        )
        return json.dumps(result, ensure_ascii=False)


def format_tool_pool_reminder(
    names: tuple[str, ...], previous: tuple[str, ...] | None = None
) -> str:
    """Build a names-only cache-stable system reminder for one agent."""
    if previous is None:
        listing = "\n".join(f"- {name}" for name in names)
        return (
            "Deferred tools available to this agent (names only):\n"
            f"{listing}\n\n"
            "Load one full schema with ToolSearch(query=\"select:<exact name>\") "
            "before calling it."
        )
    added = sorted(set(names) - set(previous))
    removed = sorted(set(previous) - set(names))
    parts = ["Deferred tool pool changed for this agent."]
    if added:
        parts.append("Added: " + ", ".join(added))
    if removed:
        parts.append("Removed: " + ", ".join(removed))
    return "\n".join(parts)
