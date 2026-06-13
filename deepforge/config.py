"""
Configuration management for DeepForge.

Supports:
- Environment variables (DEEPFORGE_API_KEY, DEEPFORGE_BASE_URL, etc.)
- YAML config file (~/.deepforge/config.yaml)
- Programmatic override
"""

import os
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _env(name: str, default: str = "", *legacy_names: str) -> str:
    for candidate in (name, *legacy_names):
        value = os.environ.get(candidate)
        if value is not None:
            return value
    return default


def _env_bool(name: str, default: bool, *legacy_names: str) -> bool:
    default_text = "1" if default else "0"
    return _env(name, default_text, *legacy_names).lower() not in {"0", "false", "no", "off"}


def _env_path(name: str, default: str, *legacy_names: str) -> Path:
    return Path(_env(name, default, *legacy_names)).expanduser()


def _env_optional_path(name: str, *legacy_names: str) -> Optional[Path]:
    value = _env(name, "", *legacy_names)
    return Path(value).expanduser() if value else None


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
    """Global configuration for a DeepForge session."""

    # ── DeepSeek API ──────────────────────────────────────────
    api_key: str = field(default_factory=lambda: _env("DEEPFORGE_API_KEY", "", "DEEPSEEK_API_KEY", "CODEX_API_KEY"))
    api_base_url: str = field(default_factory=lambda: _env("DEEPFORGE_BASE_URL", "https://api.deepseek.com/v1", "CODEX_BASE_URL"))
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

    # ── Browser Computer Use ──────────────────────────────────
    browser_enabled: bool = field(default_factory=lambda: _env_bool("DEEPFORGE_BROWSER_ENABLED", True, "CODEX_BROWSER_ENABLED"))
    browser_headless: bool = field(default_factory=lambda: _env_bool("DEEPFORGE_BROWSER_HEADLESS", False, "CODEX_BROWSER_HEADLESS"))
    browser_profile_dir: Path = field(default_factory=lambda: _env_path("DEEPFORGE_BROWSER_PROFILE_DIR", "~/.deepforge/browser-profile", "CODEX_BROWSER_PROFILE_DIR"))
    browser_screenshot_dir: Path = field(default_factory=lambda: _env_path("DEEPFORGE_BROWSER_SCREENSHOT_DIR", "~/.deepforge/browser-screenshots", "CODEX_BROWSER_SCREENSHOT_DIR"))
    browser_default_timeout_seconds: int = field(default_factory=lambda: int(_env("DEEPFORGE_BROWSER_TIMEOUT_SECONDS", "15", "CODEX_BROWSER_TIMEOUT_SECONDS")))
    browser_max_snapshot_elements: int = field(default_factory=lambda: int(_env("DEEPFORGE_BROWSER_MAX_ELEMENTS", "80", "CODEX_BROWSER_MAX_ELEMENTS")))

    # ── Audit ─────────────────────────────────────────────────
    audit_enabled: bool = field(default_factory=lambda: _env_bool("DEEPFORGE_AUDIT_ENABLED", True, "CODEX_AUDIT_ENABLED"))
    audit_dir: Path = field(default_factory=lambda: _env_path("DEEPFORGE_AUDIT_DIR", "~/.deepforge/audit", "CODEX_AUDIT_DIR"))

    # ── Sub-agent ─────────────────────────────────────────────
    sub_agent_max_depth: int = 3
    sub_agent_max_concurrent: int = 10

    # ── MCP ───────────────────────────────────────────────────
    deepforge_home: Path = field(default_factory=lambda: _env_path("DEEPFORGE_HOME", "~/.deepforge", "CUSTOM_CODEX_HOME"))
    mcp_enabled: bool = field(default_factory=lambda: _env_bool("DEEPFORGE_MCP_ENABLED", True, "CODEX_MCP_ENABLED"))
    mcp_config_path: Optional[Path] = field(default_factory=lambda: _env_optional_path("DEEPFORGE_MCP_CONFIG", "CODEX_MCP_CONFIG"))

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        mode_str = _env("DEEPFORGE_MODE", "agent", "CODEX_MODE")
        policy_str = _env("DEEPFORGE_APPROVAL", "suggest", "CODEX_APPROVAL")
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
