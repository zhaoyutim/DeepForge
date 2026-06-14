"""Synchronous facade over MCP client sessions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from deepforge.mcp.config import MCPConfig, MCPServerConfig


@dataclass
class MCPServerStatus:
    """Runtime status for an MCP server connection."""

    name: str
    transport: str
    connected: bool = False
    tool_count: int = 0
    resource_count: int = 0
    resource_template_count: int = 0
    prompt_count: int = 0
    error: Optional[str] = None


@dataclass
class _MCPConnection:
    config: MCPServerConfig
    session: Any = None
    stack: contextlib.AsyncExitStack = field(default_factory=contextlib.AsyncExitStack)
    tools: list[Any] = field(default_factory=list)
    resources: list[Any] = field(default_factory=list)
    resource_templates: list[Any] = field(default_factory=list)
    prompts: list[Any] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self.session is not None and self.error is None


class MCPClientManager:
    """Manage MCP server lifecycle and expose synchronous calls to DeepForge tools."""

    def __init__(self, config: Optional[MCPConfig] = None):
        self.config = config or MCPConfig(enabled=False)
        self._connections: dict[str, _MCPConnection] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._lock = threading.Lock()

    # ── Lifecycle ───────────────────────────────────────────────

    def initialize(self) -> None:
        """Connect to all configured MCP servers."""
        if not self.config.enabled or not self.config.active_servers:
            return

        self._ensure_loop()
        for server_config in self.config.active_servers:
            connection = _MCPConnection(config=server_config)
            self._connections[server_config.name] = connection
            try:
                self._run(self._connect(connection), timeout=server_config.timeout_seconds + 5)
            except Exception as exc:
                connection.error = str(exc)

    def close(self) -> None:
        """Close all MCP sessions and stop the background event loop."""
        if self._loop and self._connections:
            for connection in list(self._connections.values()):
                try:
                    self._run(self._close_connection(connection), timeout=10)
                except Exception:
                    pass

        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self._loop = None
        self._thread = None
        self._started = False

    # ── Discovery ───────────────────────────────────────────────

    def status(self) -> list[MCPServerStatus]:
        """Return status entries for configured MCP servers."""
        statuses: list[MCPServerStatus] = []
        for server_config in self.config.servers:
            connection = self._connections.get(server_config.name)
            statuses.append(MCPServerStatus(
                name=server_config.name,
                transport=server_config.transport,
                connected=bool(connection and connection.connected),
                tool_count=len(connection.tools) if connection else 0,
                resource_count=len(connection.resources) if connection else 0,
                resource_template_count=len(connection.resource_templates) if connection else 0,
                prompt_count=len(connection.prompts) if connection else 0,
                error=connection.error if connection else None,
            ))
        return statuses

    def connected_server_names(self) -> list[str]:
        return [name for name, connection in self._connections.items() if connection.connected]

    def get_connection(self, server_name: str) -> _MCPConnection:
        connection = self._connections.get(server_name)
        if not connection or not connection.connected:
            raise RuntimeError(f"MCP server '{server_name}' is not connected")
        return connection

    def get_tools(self, server_name: str) -> list[Any]:
        return list(self.get_connection(server_name).tools)

    def get_resources(self, server_name: str, *, refresh: bool = False) -> list[Any]:
        connection = self.get_connection(server_name)
        if refresh:
            self._run(self._refresh_resources(connection), timeout=connection.config.timeout_seconds)
        return list(connection.resources)

    def get_resource_templates(self, server_name: str, *, refresh: bool = False) -> list[Any]:
        connection = self.get_connection(server_name)
        if refresh:
            self._run(self._refresh_resource_templates(connection), timeout=connection.config.timeout_seconds)
        return list(connection.resource_templates)

    def get_prompts(self, server_name: str, *, refresh: bool = False) -> list[Any]:
        connection = self.get_connection(server_name)
        if refresh:
            self._run(self._refresh_prompts(connection), timeout=connection.config.timeout_seconds)
        return list(connection.prompts)

    # ── Calls ───────────────────────────────────────────────────

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        connection = self.get_connection(server_name)
        result = self._run(
            self._call_tool(connection, tool_name, arguments),
            timeout=connection.config.timeout_seconds,
        )
        return self._format_tool_result(result)

    def read_resource(self, server_name: str, uri: str) -> str:
        connection = self.get_connection(server_name)
        result = self._run(
            self._read_resource(connection, uri),
            timeout=connection.config.timeout_seconds,
        )
        return self._format_resource_result(result)

    def get_prompt(self, server_name: str, name: str, arguments: dict[str, Any]) -> str:
        connection = self.get_connection(server_name)
        result = self._run(
            self._get_prompt(connection, name, arguments),
            timeout=connection.config.timeout_seconds,
        )
        return self._format_prompt_result(result)

    # ── Event Loop Bridge ───────────────────────────────────────

    def _ensure_loop(self) -> None:
        with self._lock:
            if self._started:
                return
            self._loop = asyncio.new_event_loop()

            def run_loop() -> None:
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()

            self._thread = threading.Thread(target=run_loop, name="deepforge-mcp", daemon=True)
            self._thread.start()
            self._started = True

    def _run(self, coro, *, timeout: Optional[float] = None):
        if not self._loop:
            self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Async Implementation ────────────────────────────────────

    async def _connect(self, connection: _MCPConnection) -> None:
        last_error: Optional[Exception] = None
        attempts = max(1, connection.config.retry_attempts)
        for attempt in range(attempts):
            try:
                await asyncio.wait_for(
                    self._connect_once(connection),
                    timeout=connection.config.timeout_seconds,
                )
                connection.error = None
                return
            except Exception as exc:
                last_error = exc
                await connection.stack.aclose()
                connection.stack = contextlib.AsyncExitStack()
                connection.session = None
                if attempt < attempts - 1:
                    await asyncio.sleep(connection.config.retry_backoff_seconds * (attempt + 1))
        connection.error = str(last_error) if last_error else "Unknown MCP connection error"

    async def _connect_once(self, connection: _MCPConnection) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.sse import sse_client
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise RuntimeError("MCP support requires the 'mcp>=1.27,<2' package") from exc

        cfg = connection.config
        transport = cfg.transport

        if transport == "stdio":
            if not cfg.command:
                raise ValueError(f"MCP server '{cfg.name}' requires command for stdio transport")
            params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env or None)
            read_stream, write_stream = await connection.stack.enter_async_context(stdio_client(params))
        elif transport in {"streamable_http", "http"}:
            if not cfg.url:
                raise ValueError(f"MCP server '{cfg.name}' requires url for streamable_http transport")
            read_stream, write_stream, _ = await connection.stack.enter_async_context(
                streamable_http_client(cfg.url, headers=cfg.headers or None)
            )
        elif transport == "sse":
            if not cfg.url:
                raise ValueError(f"MCP server '{cfg.name}' requires url for sse transport")
            read_stream, write_stream = await connection.stack.enter_async_context(
                sse_client(
                    cfg.url,
                    headers=cfg.headers or None,
                    timeout=cfg.timeout_seconds,
                    sse_read_timeout=cfg.sse_read_timeout_seconds,
                )
            )
        else:
            raise ValueError(f"Unsupported MCP transport: {cfg.transport}")

        connection.session = await connection.stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await connection.session.initialize()
        await self._refresh_tools(connection)
        await self._refresh_resources(connection)
        await self._refresh_resource_templates(connection)
        await self._refresh_prompts(connection)

    async def _close_connection(self, connection: _MCPConnection) -> None:
        await connection.stack.aclose()
        connection.session = None

    async def _refresh_tools(self, connection: _MCPConnection) -> None:
        try:
            result = await connection.session.list_tools()
            connection.tools = list(getattr(result, "tools", []) or [])
        except Exception:
            connection.tools = []

    async def _refresh_resources(self, connection: _MCPConnection) -> None:
        try:
            result = await connection.session.list_resources()
            connection.resources = list(getattr(result, "resources", []) or [])
        except Exception:
            connection.resources = []

    async def _refresh_resource_templates(self, connection: _MCPConnection) -> None:
        try:
            result = await connection.session.list_resource_templates()
            connection.resource_templates = list(getattr(result, "resourceTemplates", []) or [])
        except Exception:
            connection.resource_templates = []

    async def _refresh_prompts(self, connection: _MCPConnection) -> None:
        try:
            result = await connection.session.list_prompts()
            connection.prompts = list(getattr(result, "prompts", []) or [])
        except Exception:
            connection.prompts = []

    async def _call_tool(self, connection: _MCPConnection, tool_name: str, arguments: dict[str, Any]) -> Any:
        return await connection.session.call_tool(tool_name, arguments=arguments or {})

    async def _read_resource(self, connection: _MCPConnection, uri: str) -> Any:
        try:
            from pydantic import AnyUrl

            resource_uri = AnyUrl(uri)
        except Exception:
            resource_uri = uri
        return await connection.session.read_resource(resource_uri)

    async def _get_prompt(self, connection: _MCPConnection, name: str, arguments: dict[str, Any]) -> Any:
        return await connection.session.get_prompt(name, arguments=arguments or {})

    # ── Formatting ──────────────────────────────────────────────

    def _format_tool_result(self, result: Any) -> tuple[str, bool]:
        parts: list[str] = []
        for item in getattr(result, "content", []) or []:
            parts.append(self._format_content_block(item))

        structured = getattr(result, "structuredContent", None)
        if structured:
            parts.append("[structured]\n" + json.dumps(self._jsonable(structured), ensure_ascii=False, indent=2))

        content = "\n".join(part for part in parts if part).strip() or "(empty MCP tool result)"
        is_error = bool(getattr(result, "isError", False))
        return content, not is_error

    def _format_resource_result(self, result: Any) -> str:
        parts = [self._format_content_block(item) for item in getattr(result, "contents", []) or []]
        return "\n".join(part for part in parts if part).strip() or "(empty MCP resource)"

    def _format_prompt_result(self, result: Any) -> str:
        parts: list[str] = []
        description = getattr(result, "description", None)
        if description:
            parts.append(str(description))
        for message in getattr(result, "messages", []) or []:
            role = getattr(message, "role", "message")
            content = self._format_content_block(getattr(message, "content", ""))
            parts.append(f"[{role}] {content}")
        return "\n".join(parts).strip() or "(empty MCP prompt)"

    def _format_content_block(self, item: Any) -> str:
        if item is None:
            return ""
        text = getattr(item, "text", None)
        if text is not None:
            return str(text)
        resource = getattr(item, "resource", None)
        if resource is not None:
            return self._format_content_block(resource)
        data = getattr(item, "data", None)
        mime_type = getattr(item, "mimeType", None) or getattr(item, "mime_type", None)
        if data is not None and mime_type:
            try:
                size = len(data)
            except TypeError:
                size = 0
            return f"[{mime_type} content, {size} bytes]"
        return json.dumps(self._jsonable(item), ensure_ascii=False, indent=2)

    def _jsonable(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [self._jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [self._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._jsonable(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "dict"):
            return value.dict()
        return str(value)
