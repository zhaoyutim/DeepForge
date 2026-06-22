"""
Session — the top-level conversation lifecycle manager.

The Session binds together:
- Agent (model + tool calling loop)
- Context Window (token tracking + compaction)
- Tool Registry (available tools)
- Approval Gate (mode × policy matrix)
- Mode management (agent / plan / yolo)
- Constitution enforcement

This is the main entry point for interactive use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from deepforge.agent import Agent, AgentResponse, ApprovalCallback
from deepforge.approval.gate import ApprovalGate
from deepforge.config import ApprovalPolicy, Backend, Mode, config
from deepforge.constitution import HierarchyResolver
from deepforge.context.window import ContextWindow
from deepforge.mcp.config import MCPConfig, default_mcp_config_path
from deepforge.mcp.manager import MCPClientManager
from deepforge.mcp.tools import build_mcp_tools
from deepforge.models.azure import AzureClient
from deepforge.models.deepseek import DeepSeekClient
from deepforge.tools.base import ToolRegistry, set_registry
from deepforge.tools.file_tools import (
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    ListDirectoryTool,
)
from deepforge.tools.search_tools import (
    GrepFilesTool,
    FileSearchTool,
    WebSearchTool,
    FetchUrlTool,
)
from deepforge.tools.shell_tools import ExecShellTool
from deepforge.tools.browser_tools import build_browser_tools
from deepforge.tools.git_tools import (
    GitStatusTool,
    GitDiffTool,
    GitLogTool,
)
from deepforge.computer.browser import close_browser_runtime


# ── System Prompt Template ──────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are DeepForge, an AI coding agent running on DeepSeek.

You have access to tools for reading/writing files, searching code, executing shell commands, and more.
For browser computer use, inspect pages with browser_snapshot and act on returned element refs like e0 instead of guessing coordinates.
When you need information from the filesystem, use the appropriate tool instead of guessing.
When you make changes, verify them by reading back the file or running relevant commands.
When the user asks you to create, implement, fix, or modify something, continue from inspection into concrete file edits and verification unless the current mode or approval policy blocks those actions.

Current workspace: {workspace}
Current mode: {mode}
Approval policy: {policy}
Backend: {backend}
Context usage: {context_usage}
MCP servers: {mcp_status}

Be direct and efficient. Execute, don't just describe."""


# ── Default Tool Registration ───────────────────────────────────────

def register_default_tools(registry: Optional[ToolRegistry] = None) -> ToolRegistry:
    """Register all default tools in the registry."""
    if registry is None:
        registry = ToolRegistry()

    default_tools = [
        # File tools
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListDirectoryTool(),
        # Search tools
        GrepFilesTool(),
        FileSearchTool(),
        WebSearchTool(),
        FetchUrlTool(),
        # Shell
        ExecShellTool(),
        # Git
        GitStatusTool(),
        GitDiffTool(),
        GitLogTool(),
    ]
    default_tools.extend(build_browser_tools())

    for tool in default_tools:
        if tool.name not in registry:
            registry.register(tool)
    return registry


# ── Session ─────────────────────────────────────────────────────────

@dataclass
class SessionConfig:
    """Per-session configuration overrides."""
    mode: Optional[Mode] = None
    approval_policy: Optional[ApprovalPolicy] = None
    workspace: Optional[Path] = None
    model: Optional[str] = None
    backend: Optional[str] = None
    config_path: Optional[Path] = None
    system_prompt: Optional[str] = None
    mcp_enabled: Optional[bool] = None
    mcp_config_path: Optional[Path] = None
    approval_callback: Optional[ApprovalCallback] = None


class Session:
    """
    Top-level session manager.

    Usage:
        session = Session()
        session.initialize()
        response = session.send("Read the README file")
        print(response.content)
    """

    def __init__(self, session_config: Optional[SessionConfig] = None):
        self.session_config = session_config or SessionConfig()

        # Apply session overrides to global config
        self.mode = self.session_config.mode or config.mode
        self.policy = self.session_config.approval_policy or config.approval_policy
        self.workspace = self.session_config.workspace or config.workspace

        # Core components (created on initialize)
        self.client: Optional[Union[DeepSeekClient, AzureClient]] = None
        self.registry: Optional[ToolRegistry] = None
        self.context: Optional[ContextWindow] = None
        self.gate: Optional[ApprovalGate] = None
        self.agent: Optional[Agent] = None
        self.mcp_manager: Optional[MCPClientManager] = None
        self.mcp_error: Optional[str] = None

        # State
        self._initialized = False
        self.session_start_time: float = 0.0
        self.total_user_messages: int = 0

        # Constitution enforcement
        self.resolver = HierarchyResolver()

    # ── Initialization ──────────────────────────────────────────

    def initialize(self) -> None:
        """Initialize all subsystems for this session."""
        if self._initialized:
            return

        # Update config
        config.mode = self.mode
        config.approval_policy = self.policy
        config.workspace = self.workspace

        # Determine backend
        if self.session_config.backend is not None:
            config.backend = Backend(self.session_config.backend)

        # Create model client based on backend
        if config.backend == Backend.AZURE:
            self.client = AzureClient(
                api_key=config.azure_api_key,
                endpoint=config.azure_endpoint,
                deployment=config.azure_deployment,
                api_version=config.azure_api_version,
                model=self.session_config.model or config.azure_model,
                reasoning_effort=config.azure_reasoning_effort,
            )
        else:
            self.client = DeepSeekClient(
                model=self.session_config.model or config.model,
            )

        self.registry = register_default_tools(ToolRegistry())
        set_registry(self.registry)
        # Use Azure-specific context window size when on Azure backend
        context_tokens = (
            config.azure_context_tokens
            if config.backend == Backend.AZURE
            else config.max_context_tokens
        )
        self.context = ContextWindow(max_tokens=context_tokens)
        self.gate = ApprovalGate(mode=self.mode, policy=self.policy)

        self._initialize_mcp()

        # Build system prompt
        system_prompt = self.session_config.system_prompt or self._build_system_prompt()
        self.context.set_system_prompt(system_prompt)

        # Create agent
        self.agent = Agent(
            client=self.client,
            registry=self.registry,
            context=self.context,
            gate=self.gate,
            approval_callback=self.session_config.approval_callback,
        )
        self.agent.system_prompt = system_prompt

        self.session_start_time = time.time()
        self._initialized = True

    def _build_system_prompt(self) -> str:
        """Build the system prompt from template."""
        return SYSTEM_PROMPT_TEMPLATE.format(
            workspace=str(self.workspace),
            mode=self.mode.value,
            policy=self.policy.value,
            backend=config.backend.value,
            context_usage=f"{self.context.usage_ratio:.0%}" if self.context else "0%",
            mcp_status=self._format_mcp_status_for_prompt(),
        )

    def _initialize_mcp(self) -> None:
        """Load MCP config, connect servers, and register MCP-backed tools."""
        mcp_enabled = self.session_config.mcp_enabled
        if mcp_enabled is None:
            mcp_enabled = config.mcp_enabled

        mcp_config_path = (
            self.session_config.mcp_config_path
            or config.mcp_config_path
            or default_mcp_config_path()
        )

        try:
            mcp_config = MCPConfig.load(Path(mcp_config_path), enabled=mcp_enabled)
            self.mcp_manager = MCPClientManager(mcp_config)
            self.mcp_manager.initialize()
            for tool in build_mcp_tools(self.mcp_manager):
                if tool.name not in self.registry:
                    self.registry.register(tool)
        except Exception as exc:
            self.mcp_error = str(exc)
            self.mcp_manager = MCPClientManager(MCPConfig(enabled=False, path=Path(mcp_config_path)))

    def _format_mcp_status_for_prompt(self) -> str:
        if self.mcp_error:
            return f"error: {self.mcp_error}"
        if not self.mcp_manager or not self.mcp_manager.config.enabled:
            return "disabled"
        statuses = self.mcp_manager.status()
        if not statuses:
            return "configured, no servers"
        parts = []
        for status in statuses:
            if status.connected:
                parts.append(
                    f"{status.name}=connected tools:{status.tool_count} "
                    f"resources:{status.resource_count} prompts:{status.prompt_count}"
                )
            else:
                parts.append(f"{status.name}=error:{status.error or 'not connected'}")
        return "; ".join(parts)

    # ── Main Interface ──────────────────────────────────────────

    def send(self, user_input: str) -> AgentResponse:
        """
        Send a message to the agent and get the response.

        This is the main entry point for user interaction.
        """
        if not self._initialized:
            self.initialize()

        self.total_user_messages += 1

        # Process through agent
        response = self.agent.process(
            user_input=user_input,
            system_prompt=self.agent.system_prompt,
        )

        # Check context pressure after the turn
        if self.context.needs_compaction:
            if self.context.is_critical:
                compact_result = self.context.compact()
                if compact_result.get("compacted"):
                    pass

        return response

    # ── Mode Management ─────────────────────────────────────────

    def set_mode(self, mode: Mode) -> None:
        """Change the agent mode."""
        self.mode = mode
        config.mode = mode
        if self.gate:
            self.gate.mode = mode

    def set_approval_policy(self, policy: ApprovalPolicy) -> None:
        """Change the approval policy."""
        self.policy = policy
        config.approval_policy = policy
        if self.gate:
            self.gate.policy = policy

    @property
    def is_read_only(self) -> bool:
        """True when the session is in read-only mode."""
        return config.is_read_only

    # ── Context Management ──────────────────────────────────────

    def compact(self) -> dict:
        """Trigger context compaction."""
        if self.context:
            return self.context.compact()
        return {"compacted": False, "reason": "No context window"}

    def get_context_stats(self) -> dict:
        """Get context window statistics."""
        if self.context:
            return self.context.stats()
        return {}

    # ── Tool Management ─────────────────────────────────────────

    def register_tool(self, tool) -> None:
        """Register an additional tool."""
        if self.registry:
            self.registry.register(tool)

    @property
    def available_tools(self) -> list[str]:
        """List available tool names."""
        if self.registry:
            return self.registry.tool_names
        return []

    def mcp_status(self) -> dict:
        """Get MCP runtime status."""
        if self.mcp_error:
            return {"enabled": False, "error": self.mcp_error, "servers": []}
        if not self.mcp_manager:
            return {"enabled": False, "servers": []}
        return {
            "enabled": self.mcp_manager.config.enabled,
            "config_path": str(self.mcp_manager.config.path),
            "servers": [status.__dict__ for status in self.mcp_manager.status()],
        }

    def reload_mcp(self) -> dict:
        """Reload MCP config and rebuild the tool registry."""
        if self.mcp_manager:
            self.mcp_manager.close()

        self.mcp_error = None
        self.registry = register_default_tools(ToolRegistry())
        set_registry(self.registry)
        self._initialize_mcp()

        if self.agent:
            self.agent.registry = self.registry
            self.agent.dispatcher.registry = self.registry
            system_prompt = self.session_config.system_prompt or self._build_system_prompt()
            self.agent.system_prompt = system_prompt
            if self.context:
                self.context.set_system_prompt(system_prompt)
        return self.mcp_status()

    def close(self) -> None:
        """Close session-owned resources."""
        if self.mcp_manager:
            self.mcp_manager.close()
        close_browser_runtime()

    # ── Stats ────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Session statistics."""
        ctx_stats = self.get_context_stats()
        return {
            "mode": self.mode.value,
            "policy": self.policy.value,
            "backend": config.backend.value,
            "workspace": str(self.workspace),
            "initialized": self._initialized,
            "total_messages": self.total_user_messages,
            "tools_available": len(self.available_tools),
            "mcp": self.mcp_status(),
            "context": ctx_stats,
            "reasoning_effort": getattr(self.client, "reasoning_effort", None) if self.client else None,
            "azure_context_tokens": config.azure_context_tokens,
            "effective_context_tokens": ctx_stats.get("max_tokens", 0),
            "uptime_seconds": time.time() - self.session_start_time if self.session_start_time else 0,
            "api_requests": self.client.total_requests if self.client else 0,
            "api_tokens_used": self.client.total_tokens_used if self.client else 0,
        }

    def __repr__(self) -> str:
        return (
            f"<Session mode={self.mode.value} policy={self.policy.value} "
            f"backend={config.backend.value} "
            f"tools={len(self.available_tools)} "
            f"context={self.context}>"
        )
