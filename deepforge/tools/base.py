"""
Tool base class and registry.

Every tool inherits from BaseTool. The ToolRegistry manages tool discovery
and schema generation for the DeepSeek API.
"""

from __future__ import annotations

import inspect
import copy
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from deepforge.types import ToolCall, ToolResult, ToolSchema


class BaseTool(ABC):
    """
    Abstract base class for all DeepForge tools.

    Subclasses must:
    1. Set `name`, `description`, and `parameters` (JSON Schema)
    2. Set `is_read`, `is_write`, `is_shell`, `is_network` flags
    3. Implement `execute(tool_call) -> ToolResult`
    """

    name: str
    description: str
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    argument_aliases: dict[str, list[str]] = {}

    # Approval gating flags
    is_read: bool = True
    is_write: bool = False
    is_shell: bool = False
    is_network: bool = False
    requires_user_approval: bool = False

    # Workspace context (set by the session)
    workspace: Optional[str] = None

    @abstractmethod
    def execute(self, tool_call: ToolCall) -> ToolResult:
        """
        Execute the tool with the given arguments.

        Args:
            tool_call: The tool call from the model, with function_name and arguments.

        Returns:
            ToolResult with the execution output.
        """
        ...

    def to_schema(self) -> ToolSchema:
        """Convert to a ToolSchema for API registration."""
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            is_read=self.is_read,
            is_write=self.is_write,
            is_shell=self.is_shell,
            is_network=self.is_network,
        )

    @property
    def requires_approval(self) -> bool:
        """True if this tool requires user approval before execution."""
        return self.requires_user_approval or self.is_write or self.is_shell

    def clone(self) -> "BaseTool":
        """Create a copy of this tool for an isolated registry."""
        return copy.copy(self)

    def validate_args(self, tool_call: ToolCall) -> Optional[str]:
        """
        Validate that required arguments are present.
        Returns None if valid, or an error message string.
        """
        required = self.parameters.get("required", [])
        for param in required:
            if param not in tool_call.arguments:
                received = ", ".join(sorted(tool_call.arguments.keys())) or "no arguments"
                return f"Missing required parameter: '{param}' (received: {received})"
        return None

    def normalize_args(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return arguments with known aliases copied to canonical names."""
        normalized = dict(arguments)
        compact_arguments = {
            re.sub(r"[^a-z0-9]", "", str(key).lower()): value
            for key, value in arguments.items()
        }
        for canonical_name, aliases in self.argument_aliases.items():
            if canonical_name in normalized:
                continue
            for alias in aliases:
                if alias in normalized:
                    normalized[canonical_name] = normalized[alias]
                    break
            else:
                for candidate in (canonical_name, *aliases):
                    compact_name = re.sub(r"[^a-z0-9]", "", candidate.lower())
                    if compact_name in compact_arguments:
                        normalized[canonical_name] = compact_arguments[compact_name]
                        break
        return normalized

    def __repr__(self) -> str:
        flags = []
        if self.is_read:
            flags.append("R")
        if self.is_write:
            flags.append("W")
        if self.is_shell:
            flags.append("S")
        if self.is_network:
            flags.append("N")
        return f"<{self.name} [{''.join(flags)}]>"


class ToolRegistry:
    """
    Central registry for all available tools.

    Handles:
    - Tool registration and lookup
    - Schema generation for the API
    - Tool filtering by category
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def register_many(self, tools: list[BaseTool]) -> None:
        """Register multiple tools at once."""
        for tool in tools:
            self.register(tool)

    def _resolve_tool_name(self, name: str) -> Optional[str]:
        """Resolve a tool name, accepting common punctuation/casing aliases."""
        if name in self._tools:
            return name

        snake_name = str(name).strip().lower().replace("-", "_").replace(" ", "_")
        if snake_name in self._tools:
            return snake_name

        compact_name = re.sub(r"[^a-z0-9]", "", snake_name)
        if not compact_name:
            return None

        matches = [
            tool_name
            for tool_name in self._tools
            if re.sub(r"[^a-z0-9]", "", tool_name.lower()) == compact_name
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def clone_filtered(self, allowed_tools: Optional[list[str]] = None) -> "ToolRegistry":
        """Create a new registry containing clones of selected tools."""
        cloned = ToolRegistry()
        names = self.tool_names if allowed_tools is None else allowed_tools
        for name in names:
            tool = self.get(name)
            if tool:
                cloned.register(tool.clone())
        return cloned

    def get(self, name: str) -> Optional[BaseTool]:
        """Look up a tool by name."""
        resolved_name = self._resolve_tool_name(name)
        if resolved_name is None:
            return None
        return self._tools.get(resolved_name)

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """
        Execute a tool call by name.

        Returns an error ToolResult if the tool is not found or fails.
        """
        resolved_name = self._resolve_tool_name(tool_call.function_name)
        tool = self._tools.get(resolved_name) if resolved_name else None
        if tool is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: Unknown tool '{tool_call.function_name}'",
                success=False,
                error=f"Tool '{tool_call.function_name}' not found in registry",
            )

        canonical_call = ToolCall(
            id=tool_call.id,
            function_name=resolved_name or tool_call.function_name,
            arguments=tool.normalize_args(tool_call.arguments),
        )

        parse_error = canonical_call.arguments.get("_tool_parse_error")
        if parse_error:
            return ToolResult(
                tool_call_id=canonical_call.id,
                content=f"Error: {parse_error}",
                success=False,
                error=str(parse_error),
                tool_name=resolved_name,
            )

        # Validate arguments
        validation_error = tool.validate_args(canonical_call)
        if validation_error:
            return ToolResult(
                tool_call_id=canonical_call.id,
                content=f"Error: {validation_error}",
                success=False,
                error=validation_error,
            )

        try:
            result = tool.execute(canonical_call)
            result.tool_name = resolved_name
            return result
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error executing {tool.name}: {e}",
                success=False,
                error=str(e),
                tool_name=tool_call.function_name,
            )

    def get_schemas(self) -> list[ToolSchema]:
        """Get all registered tool schemas (for API requests)."""
        return [tool.to_schema() for tool in self._tools.values()]

    def get_read_tools(self) -> list[BaseTool]:
        """Get only read-only tools."""
        return [t for t in self._tools.values() if t.is_read and not t.is_write]

    def get_write_tools(self) -> list[BaseTool]:
        """Get tools that modify the filesystem."""
        return [t for t in self._tools.values() if t.is_write]

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools.keys())

    @property
    def count(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        reads = len(self.get_read_tools())
        writes = len(self.get_write_tools())
        return f"<ToolRegistry: {len(self)} tools ({reads}R, {writes}W)>"


# ── Global registry ──────────────────────────────────────────────────

_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """Get or create the global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def set_registry(registry: ToolRegistry) -> None:
    """Set the global registry used by convenience APIs."""
    global _registry
    _registry = registry
