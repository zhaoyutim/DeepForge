"""
Configuration management for CodeX.

Supports:
- Environment variables (CODEX_API_KEY, CODEX_BASE_URL, etc.)
- YAML config file (~/.codex/config.yaml)
- Programmatic override
"""

import os
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class Mode(str, Enum):
    """Agent operation mode."""
    AGENT = "agent"    # Autonomous task execution
    PLAN = "plan"      # Design before implementing (read-only)
    YOLO = "yolo"      # Full autonomy, all actions pre-approved


class ApprovalPolicy(str, Enum):
    """Tool execution approval policy."""
    AUTO = "auto"      # All tools auto-approved
    SUGGEST = "suggest"  # Write tools require approval
    NEVER = "never"    # All writes blocked


@dataclass
class Config:
    """Global configuration for a CodeX session."""

    # ── DeepSeek API ──────────────────────────────────────────
    api_key: str = field(default_factory=lambda: os.environ.get("CODEX_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "")))
    api_base_url: str = field(default_factory=lambda: os.environ.get("CODEX_BASE_URL", "https://api.deepseek.com/v1"))
    model: str = "deepseek-chat"  # or deepseek-reasoner for R1

    # ── Mode & Approval ───────────────────────────────────────
    mode: Mode = Mode.AGENT
    approval_policy: ApprovalPolicy = ApprovalPolicy.SUGGEST

    # ── Workspace ─────────────────────────────────────────────
    workspace: Path = field(default_factory=Path.cwd)

    # ── Context Window ────────────────────────────────────────
    max_context_tokens: int = 1_000_000  # DeepSeek V4 1M window
    compaction_threshold: float = 0.6     # Compact at 60% usage
    prefix_cache_granularity: int = 128   # 128-token cache blocks

    # ── Tool Execution ────────────────────────────────────────
    shell_enabled: bool = True
    max_parallel_tools: int = 10
    tool_timeout_seconds: int = 30

    # ── Sub-agent ─────────────────────────────────────────────
    sub_agent_max_depth: int = 3
    sub_agent_max_concurrent: int = 10

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        mode_str = os.environ.get("CODEX_MODE", "agent")
        policy_str = os.environ.get("CODEX_APPROVAL", "suggest")
        return cls(
            mode=Mode(mode_str),
            approval_policy=ApprovalPolicy(policy_str),
        )

    @property
    def is_read_only(self) -> bool:
        """True when the current policy blocks all writes."""
        return self.approval_policy == ApprovalPolicy.NEVER or self.mode == Mode.PLAN

    @property
    def requires_approval(self) -> bool:
        """True when write operations require user approval."""
        return self.approval_policy == ApprovalPolicy.SUGGEST


# Global config instance (can be overridden per-session)
config = Config.from_env()
