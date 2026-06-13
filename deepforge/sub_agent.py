"""
Sub-Agent subsystem — isolated child sessions for parallel work.

Implements the CodeWhale sub-agent pattern:
- Each sub-agent gets its own context window (doesn't bloat parent)
- Independent tool registry (can be restricted)
- Runs in a separate thread
- Returns results to the parent session
- Supports depth limits (max recursion)

This enables parallel investigation and implementation without
exploding the parent's context window.
"""

from __future__ import annotations

import concurrent.futures
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

from deepforge.agent import Agent, AgentResponse
from deepforge.config import ApprovalPolicy, Mode, config
from deepforge.context.window import ContextWindow
from deepforge.models.deepseek import DeepSeekClient, get_client
from deepforge.models.tokenizer import count_tokens
from deepforge.tools.base import ToolRegistry, get_registry


@dataclass
class SubAgentConfig:
    """Configuration for a sub-agent."""
    name: str = ""
    prompt: str = ""
    allowed_tools: Optional[list[str]] = None  # None = all tools
    max_depth: int = 1  # Recursion limit
    model: Optional[str] = None
    workspace: Optional[str] = None


@dataclass
class SubAgentResult:
    """Result from a sub-agent execution."""
    agent_id: str
    name: str
    content: str
    success: bool
    error: Optional[str] = None
    tool_calls_made: int = 0
    tokens_used: int = 0
    latency_ms: float = 0.0


class SubAgentRunner:
    """
    Manages sub-agent creation and execution.

    Usage:
        runner = SubAgentRunner()
        result = runner.run(SubAgentConfig(
            name="explorer",
            prompt="Read and summarize README.md",
            allowed_tools=["read_file", "list_dir"],
        ))
    """

    def __init__(
        self,
        max_concurrent: Optional[int] = None,
        max_depth: Optional[int] = None,
        parent_registry: Optional[ToolRegistry] = None,
    ):
        self.max_concurrent = max_concurrent or config.sub_agent_max_concurrent
        self.max_depth = max_depth or config.sub_agent_max_depth
        self.parent_registry = parent_registry
        self._lock = threading.Lock()
        self.active_agents: dict[str, SubAgentResult] = {}

    # ── Single Sub-Agent ─────────────────────────────────────────

    def run(self, config: SubAgentConfig) -> SubAgentResult:
        """
        Run a single sub-agent synchronously.

        Creates an independent session with its own context and tools.
        """
        agent_id = str(uuid.uuid4())[:8]
        name = config.name or f"sub-agent-{agent_id}"

        # Create isolated components
        client = DeepSeekClient(
            model=config.model,
        )

        registry = self._create_restricted_registry(config.allowed_tools)

        context = ContextWindow()

        # Build sub-agent system prompt
        system_prompt = (
            f"You are a sub-agent ({name}) in the DeepForge system.\n"
            f"Your task: {config.prompt}\n"
            f"Execute your assigned task efficiently and return a concise result.\n"
            f"Do not ask follow-up questions — complete the task and report."
        )

        # Create agent
        agent = Agent(
            client=client,
            registry=registry,
            context=context,
        )

        # Process
        try:
            response = agent.process(
                user_input=config.prompt,
                system_prompt=system_prompt,
            )

            result = SubAgentResult(
                agent_id=agent_id,
                name=name,
                content=response.content,
                success=response.success,
                error=response.error,
                tool_calls_made=len(response.tool_results),
                tokens_used=response.total_tokens_used,
                latency_ms=response.latency_ms,
            )
        except Exception as e:
            result = SubAgentResult(
                agent_id=agent_id,
                name=name,
                content=f"Error: {e}",
                success=False,
                error=str(e),
            )

        return result

    # ── Parallel Sub-Agents ──────────────────────────────────────

    def run_parallel(self, configs: list[SubAgentConfig]) -> list[SubAgentResult]:
        """
        Run multiple sub-agents in parallel.

        All sub-agents run concurrently in separate threads.
        Results are returned in the same order as configs.
        """
        if not configs:
            return []

        max_workers = min(self.max_concurrent, len(configs))
        results_map: dict[int, SubAgentResult] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for i, cfg in enumerate(configs):
                future = executor.submit(self.run, cfg)
                futures[future] = i

            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                try:
                    result = future.result(timeout=300)  # 5 min timeout per sub-agent
                    results_map[idx] = result
                except Exception as e:
                    results_map[idx] = SubAgentResult(
                        agent_id="error",
                        name=configs[idx].name or f"sub-agent-{idx}",
                        content=f"Sub-agent failed: {e}",
                        success=False,
                        error=str(e),
                    )

        # Return in original order
        return [results_map[i] for i in range(len(configs))]

    # ── Tool Restriction ─────────────────────────────────────────

    def _create_restricted_registry(
        self,
        allowed_tools: Optional[list[str]] = None,
    ) -> ToolRegistry:
        """
        Create a tool registry for a sub-agent.

        If allowed_tools is None, use all tools from the parent registry.
        Otherwise, only include the named tools.
        """
        parent_registry = self.parent_registry or get_registry()
        return parent_registry.clone_filtered(allowed_tools)


# ── Convenience Functions ───────────────────────────────────────────

def create_sub_agent(
    prompt: str,
    name: Optional[str] = None,
    allowed_tools: Optional[list[str]] = None,
) -> SubAgentResult:
    """
    Quick one-shot sub-agent execution.

    Example:
        result = create_sub_agent(
            prompt="Read README.md and return the project description",
            name="reader",
            allowed_tools=["read_file", "list_dir"],
        )
    """
    runner = SubAgentRunner()
    return runner.run(SubAgentConfig(
        name=name or "sub-agent",
        prompt=prompt,
        allowed_tools=allowed_tools,
    ))


def parallel_investigate(
    tasks: list[tuple[str, str, Optional[list[str]]]],
) -> list[SubAgentResult]:
    """
    Run multiple investigation tasks in parallel.

    Args:
        tasks: List of (name, prompt, allowed_tools) tuples

    Example:
        results = parallel_investigate([
            ("read-config", "Read pyproject.toml and summarize", ["read_file"]),
            ("check-deps", "List all requirements files", ["file_search"]),
            ("git-log", "Show recent 5 commits", ["git_log"]),
        ])
    """
    runner = SubAgentRunner()
    configs = [
        SubAgentConfig(name=name, prompt=prompt, allowed_tools=tools)
        for name, prompt, tools in tasks
    ]
    return runner.run_parallel(configs)
