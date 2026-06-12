"""
Tool base class and registry.

Every tool inherits from BaseTool. The ToolRegistry manages tool discovery
and schema generation for the DeepSeek API.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any, Optional

from codex.types import ToolCall, ToolResult, ToolSchema


class BaseTool(ABC):
    """
    Abstract base class for all CodeX tools.

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

    # Approval gating flags
    is_read: bool = True
    is_write: bool = False
    is_shell: bool = False
    is_network: bool = False

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
        return self.is_write or self.is_shell

    def validate_args(self, tool_call: ToolCall) -> Optional[str]:
        """
        Validate that required arguments are present.
        Returns None if valid, or an error message string.
        """
        required = self.parameters.get("required", [])
        for param in required:
            if param not in tool_call.arguments:
                return f"Missing required parameter: '{param}'"
        return None

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

    def get(self, name: str) -> Optional[BaseTool]:
        """Look up a tool by name."""
        return self._tools.get(name)

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """
        Execute a tool call by name.

        Returns an error ToolResult if the tool is not found or fails.
        """
        tool = self._tools.get(tool_call.function_name)
        if tool is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: Unknown tool '{tool_call.function_name}'",
                success=False,
                error=f"Tool '{tool_call.function_name}' not found in registry",
            )

        # Validate arguments
        validation_error = tool.validate_args(tool_call)
        if validation_error:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: {validation_error}",
                success=False,
                error=validation_error,
            )

        try:
            return tool.execute(tool_call)
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error executing {tool.name}: {e}",
                success=False,
                error=str(e),
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
