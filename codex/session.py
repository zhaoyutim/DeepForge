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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from codex.agent import Agent, AgentResponse
from codex.approval.gate import ApprovalGate, GateDecision
from codex.config import ApprovalPolicy, Mode, config
from codex.constitution import HierarchyResolver, Tier, enforce_verification
from codex.context.window import ContextWindow
from codex.models.deepseek import DeepSeekClient, get_client
from codex.tools.base import ToolRegistry, get_registry
from codex.tools.file_tools import (
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    ListDirectoryTool,
)
from codex.tools.search_tools import (
    GrepFilesTool,
    FileSearchTool,
    WebSearchTool,
    FetchUrlTool,
)
from codex.tools.shell_tools import ExecShellTool
from codex.tools.git_tools import (
    GitStatusTool,
    GitDiffTool,
    GitLogTool,
)


# ── System Prompt Template ──────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are CodeX, an AI coding agent running on DeepSeek.

You have access to tools for reading/writing files, searching code, executing shell commands, and more.
When you need information from the filesystem, use the appropriate tool instead of guessing.
When you make changes, verify them by reading back the file or running relevant commands.
When the user asks you to create, implement, fix, or modify something, continue from inspection into concrete file edits and verification unless the current mode or approval policy blocks those actions.

Current workspace: {workspace}
Current mode: {mode}
Approval policy: {policy}
Context usage: {context_usage}

Be direct and efficient. Execute, don't just describe."""


# ── Default Tool Registration ───────────────────────────────────────

def register_default_tools(registry: Optional[ToolRegistry] = None) -> ToolRegistry:
    """Register all default tools in the registry."""
    if registry is None:
        registry = get_registry()

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

    registry.register_many(default_tools)
    return registry


# ── Session ─────────────────────────────────────────────────────────

@dataclass
class SessionConfig:
    """Per-session configuration overrides."""
    mode: Optional[Mode] = None
    approval_policy: Optional[ApprovalPolicy] = None
    workspace: Optional[Path] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None


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
        self.client: Optional[DeepSeekClient] = None
        self.registry: Optional[ToolRegistry] = None
        self.context: Optional[ContextWindow] = None
        self.gate: Optional[ApprovalGate] = None
        self.agent: Optional[Agent] = None

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

        # Create components
        self.client = DeepSeekClient(
            model=self.session_config.model,
        )
        self.registry = register_default_tools()
        self.context = ContextWindow()
        self.gate = ApprovalGate(mode=self.mode, policy=self.policy)

        # Build system prompt
        system_prompt = self.session_config.system_prompt or self._build_system_prompt()
        self.context.set_system_prompt(system_prompt)

        # Create agent
        self.agent = Agent(
            client=self.client,
            registry=self.registry,
            context=self.context,
            gate=self.gate,
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
            context_usage=f"{self.context.usage_ratio:.0%}" if self.context else "0%",
        )

    # ── Main Interface ──────────────────────────────────────────

    def send(self, user_input: str) -> AgentResponse:
        """
        Send a message to the agent and get the response.

        This is the main entry point for user interaction.
        """
        if not self._initialized:
            self.initialize()

        self.total_user_messages += 1

        # Constitution Article II check: truth enforcement
        # (In practice, this is enforced by the agent using tools)

        # Process through agent
        response = self.agent.process(
            user_input=user_input,
            system_prompt=self.agent.system_prompt,
        )

        # Check context pressure after the turn
        if self.context.needs_compaction:
            # In interactive mode, we'd prompt the user
            # For now, auto-compact if critical
            if self.context.is_critical:
                compact_result = self.context.compact()
                if compact_result.get("compacted"):
                    # Update agent's context reference
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

    # ── Stats ────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Session statistics."""
        return {
            "mode": self.mode.value,
            "policy": self.policy.value,
            "workspace": str(self.workspace),
            "initialized": self._initialized,
            "total_messages": self.total_user_messages,
            "tools_available": len(self.available_tools),
            "context": self.get_context_stats(),
            "uptime_seconds": time.time() - self.session_start_time if self.session_start_time else 0,
            "api_requests": self.client.total_requests if self.client else 0,
            "api_tokens_used": self.client.total_tokens_used if self.client else 0,
        }

    def __repr__(self) -> str:
        return (
            f"<Session mode={self.mode.value} policy={self.policy.value} "
            f"tools={len(self.available_tools)} "
            f"context={self.context}>"
        )
