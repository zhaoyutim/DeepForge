"""MCP client integration for DeepForge."""

from deepforge.mcp.config import MCPConfig, MCPServerConfig
from deepforge.mcp.manager import MCPClientManager, MCPServerStatus
from deepforge.mcp.tools import (
    build_mcp_tools,
    MCPGetPromptTool,
    MCPListPromptsTool,
    MCPListResourceTemplatesTool,
    MCPListResourcesTool,
    MCPReadResourceTool,
    MCPRemoteTool,
)

__all__ = [
    "MCPClientManager",
    "MCPConfig",
    "MCPServerConfig",
    "MCPServerStatus",
    "MCPRemoteTool",
    "build_mcp_tools",
    "MCPListResourcesTool",
    "MCPReadResourceTool",
    "MCPListResourceTemplatesTool",
    "MCPListPromptsTool",
    "MCPGetPromptTool",
]