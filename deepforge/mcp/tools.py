"""Adapters that expose MCP capabilities as DeepForge tools."""

from __future__ import annotations

import json
import re
from typing import Any

from deepforge.mcp.config import MCPToolOverride
from deepforge.mcp.manager import MCPClientManager
from deepforge.tools.base import BaseTool
from deepforge.types import ToolCall, ToolResult


def safe_tool_part(value: str) -> str:
    """Make a stable function-name-safe component."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned.lower() or "unnamed"


def mcp_tool_name(server_name: str, remote_name: str) -> str:
    return f"mcp__{safe_tool_part(server_name)}__{safe_tool_part(remote_name)}"


def _get_attr_or_key(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _schema_from_tool(tool: Any) -> dict[str, Any]:
    schema = _get_attr_or_key(tool, "inputSchema") or _get_attr_or_key(tool, "input_schema")
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_dump"):
        return schema.model_dump(mode="json")
    return {"type": "object", "properties": {}, "required": []}


def _description_from_tool(server_name: str, remote_name: str, tool: Any) -> str:
    description = _get_attr_or_key(tool, "description") or "Remote MCP tool."
    title = _get_attr_or_key(tool, "title")
    if title:
        return f"MCP tool from {server_name}: {title}. {description}"
    return f"MCP tool from {server_name}: {description}"


def _infer_safety(tool: Any, override: MCPToolOverride) -> dict[str, bool]:
    annotations = _get_attr_or_key(tool, "annotations") or {}
    read_only = bool(_get_attr_or_key(annotations, "readOnlyHint", False))
    destructive = bool(_get_attr_or_key(annotations, "destructiveHint", False))
    open_world = bool(_get_attr_or_key(annotations, "openWorldHint", False))

    is_read = read_only and not destructive
    is_write = destructive or not read_only
    is_network = open_world
    requires_approval = not read_only or destructive or open_world

    values = {
        "is_read": is_read,
        "is_write": is_write,
        "is_shell": False,
        "is_network": is_network,
        "requires_approval": requires_approval,
    }
    for field_name in values:
        override_value = getattr(override, field_name)
        if override_value is not None:
            values[field_name] = override_value
    return values


class MCPRemoteTool(BaseTool):
    """BaseTool wrapper around an MCP server tool."""

    def __init__(
        self,
        manager: MCPClientManager,
        server_name: str,
        remote_tool: Any,
        override: MCPToolOverride,
    ):
        remote_name = str(_get_attr_or_key(remote_tool, "name", "unnamed"))
        safety = _infer_safety(remote_tool, override)
        self.manager = manager
        self.server_name = server_name
        self.remote_name = remote_name
        self.name = mcp_tool_name(server_name, remote_name)
        self.description = _description_from_tool(server_name, remote_name, remote_tool)
        self.parameters = _schema_from_tool(remote_tool)
        self.is_read = safety["is_read"]
        self.is_write = safety["is_write"]
        self.is_shell = safety["is_shell"]
        self.is_network = safety["is_network"]
        self.requires_user_approval = safety["requires_approval"]

    def execute(self, tool_call: ToolCall) -> ToolResult:
        content, success = self.manager.call_tool(
            self.server_name,
            self.remote_name,
            tool_call.arguments,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            content=content,
            success=success,
            error=None if success else content,
        )


class _MCPServerTool(BaseTool):
    """Shared base for server-scoped MCP helper tools."""

    is_read = True
    is_write = False
    is_shell = False
    is_network = True

    def __init__(self, manager: MCPClientManager, server_name: str):
        self.manager = manager
        self.server_name = server_name
        self.server_part = safe_tool_part(server_name)

    def _result(self, tool_call: ToolCall, content: str, success: bool = True) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call.id,
            content=content,
            success=success,
            error=None if success else content,
        )

    def _format_items(self, items: list[Any], fields: list[str]) -> str:
        if not items:
            return "(none)"
        rows = []
        for item in items:
            data = {}
            for field in fields:
                value = _get_attr_or_key(item, field)
                if value is not None:
                    data[field] = str(value)
            rows.append(data or str(item))
        return json.dumps(rows, ensure_ascii=False, indent=2)


class MCPListResourcesTool(_MCPServerTool):
    parameters = {
        "type": "object",
        "properties": {
            "refresh": {"type": "boolean", "description": "Refresh the server resource list first"},
        },
        "required": [],
    }

    def __init__(self, manager: MCPClientManager, server_name: str):
        super().__init__(manager, server_name)
        self.name = f"mcp__{self.server_part}__list_resources"
        self.description = f"List MCP resources exposed by server '{server_name}'."

    def execute(self, tool_call: ToolCall) -> ToolResult:
        items = self.manager.get_resources(self.server_name, refresh=bool(tool_call.arguments.get("refresh", False)))
        return self._result(tool_call, self._format_items(items, ["uri", "name", "description", "mimeType"]))


class MCPReadResourceTool(_MCPServerTool):
    parameters = {
        "type": "object",
        "properties": {
            "uri": {"type": "string", "description": "MCP resource URI to read"},
        },
        "required": ["uri"],
    }

    def __init__(self, manager: MCPClientManager, server_name: str):
        super().__init__(manager, server_name)
        self.name = f"mcp__{self.server_part}__read_resource"
        self.description = f"Read one MCP resource from server '{server_name}'."

    def execute(self, tool_call: ToolCall) -> ToolResult:
        uri = str(tool_call.arguments.get("uri", ""))
        return self._result(tool_call, self.manager.read_resource(self.server_name, uri))


class MCPListResourceTemplatesTool(_MCPServerTool):
    parameters = {
        "type": "object",
        "properties": {
            "refresh": {"type": "boolean", "description": "Refresh the server resource template list first"},
        },
        "required": [],
    }

    def __init__(self, manager: MCPClientManager, server_name: str):
        super().__init__(manager, server_name)
        self.name = f"mcp__{self.server_part}__list_resource_templates"
        self.description = f"List MCP resource templates exposed by server '{server_name}'."

    def execute(self, tool_call: ToolCall) -> ToolResult:
        items = self.manager.get_resource_templates(
            self.server_name,
            refresh=bool(tool_call.arguments.get("refresh", False)),
        )
        return self._result(tool_call, self._format_items(items, ["uriTemplate", "name", "description", "mimeType"]))


class MCPListPromptsTool(_MCPServerTool):
    parameters = {
        "type": "object",
        "properties": {
            "refresh": {"type": "boolean", "description": "Refresh the server prompt list first"},
        },
        "required": [],
    }

    def __init__(self, manager: MCPClientManager, server_name: str):
        super().__init__(manager, server_name)
        self.name = f"mcp__{self.server_part}__list_prompts"
        self.description = f"List MCP prompts exposed by server '{server_name}'."

    def execute(self, tool_call: ToolCall) -> ToolResult:
        items = self.manager.get_prompts(self.server_name, refresh=bool(tool_call.arguments.get("refresh", False)))
        return self._result(tool_call, self._format_items(items, ["name", "title", "description"]))


class MCPGetPromptTool(_MCPServerTool):
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Prompt name"},
            "arguments": {
                "type": "object",
                "description": "Prompt arguments as a JSON object",
                "additionalProperties": True,
            },
        },
        "required": ["name"],
    }

    def __init__(self, manager: MCPClientManager, server_name: str):
        super().__init__(manager, server_name)
        self.name = f"mcp__{self.server_part}__get_prompt"
        self.description = f"Fetch/render one MCP prompt from server '{server_name}'."

    def execute(self, tool_call: ToolCall) -> ToolResult:
        name = str(tool_call.arguments.get("name", ""))
        arguments = tool_call.arguments.get("arguments", {}) or {}
        return self._result(tool_call, self.manager.get_prompt(self.server_name, name, arguments))


def build_mcp_tools(manager: MCPClientManager) -> list[BaseTool]:
    """Build DeepForge tools for every connected MCP capability."""
    tools: list[BaseTool] = []
    for server_name in manager.connected_server_names():
        connection = manager.get_connection(server_name)
        overrides = connection.config.tool_overrides
        for remote_tool in manager.get_tools(server_name):
            remote_name = str(_get_attr_or_key(remote_tool, "name", "unnamed"))
            tools.append(MCPRemoteTool(
                manager=manager,
                server_name=server_name,
                remote_tool=remote_tool,
                override=overrides.get(remote_name, MCPToolOverride()),
            ))
        tools.extend([
            MCPListResourcesTool(manager, server_name),
            MCPReadResourceTool(manager, server_name),
            MCPListResourceTemplatesTool(manager, server_name),
            MCPListPromptsTool(manager, server_name),
            MCPGetPromptTool(manager, server_name),
        ])
    return tools
