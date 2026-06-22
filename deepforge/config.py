"""
Configuration management for DeepForge.

Supports:
- Environment variables (DEEPFORGE_API_KEY, DEEPFORGE_BASE_URL, etc.)
- YAML config file (config/env.yaml by default)
- CLI argument override

Priority: CLI args > env.yaml > environment variables > defaults
"""

import os
import sys
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


def _discover_config_path() -> Optional[Path]:
    """Find config/env.yaml in the working directory tree."""
    # 1. Explicit env var
    explicit = _env_optional_path("DEEPFORGE_CONFIG", "CODEX_CONFIG")
    if explicit and explicit.exists():
        return explicit

    # 2. project's config/env.yaml
    cwd = Path.cwd()
    candidate = cwd / "config" / "env.yaml"
    if candidate.exists():
        return candidate

    # 3. ~/.deepforge/config.yaml
    home_candidate = Path.home() / ".deepforge" / "config.yaml"
    if home_candidate.exists():
        return home_candidate

    return None


def _load_yaml_config(config_path: Optional[Path]) -> dict:
    """Load a YAML config file, returning empty dict on any failure."""
    if config_path is None or not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class Mode(str, Enum):
    """Agent operation mode."""
    AGENT = "agent"      # Autonomous task execution
    PLAN = "plan"        # Design before implementing (read-only)
    YOLO = "yolo"        # Full autonomy, all actions pre-approved


class ApprovalPolicy(str, Enum):
    """Tool execution approval policy."""
    AUTO = "auto"        # All tools auto-approved
    SUGGEST = "suggest"  # Write tools require approval
    NEVER = "never"      # All writes blocked


class Backend(str, Enum):
    """Model backend provider."""
    DEEPSEEK = "deepseek"
    AZURE = "azure"


@dataclass
class Config:
    """Global configuration for a DeepForge session."""

    # ── Backend Selection ────────────────────────────────────
    backend: Backend = Backend.DEEPSEEK

    # ── DeepSeek API ──────────────────────────────────────────
    api_key: str = field(default_factory=lambda: _env("DEEPFORGE_API_KEY", "", "DEEPSEEK_API_KEY", "CODEX_API_KEY"))
    api_base_url: str = field(default_factory=lambda: _env("DEEPFORGE_BASE_URL", "https://api.deepseek.com/v1", "CODEX_BASE_URL"))
    model: str = "deepseek-chat"  # or deepseek-reasoner for R1

    # ── Azure OpenAI API ──────────────────────────────────────
    azure_api_key: str = field(default_factory=lambda: _env("AZURE_OPENAI_API_KEY", "", "DEEPFORGE_AZURE_API_KEY", "CODEX_AZURE_API_KEY"))
    azure_endpoint: str = field(default_factory=lambda: _env("AZURE_OPENAI_ENDPOINT", "", "DEEPFORGE_AZURE_ENDPOINT", "CODEX_AZURE_ENDPOINT"))
    azure_deployment: str = field(default_factory=lambda: _env("AZURE_OPENAI_DEPLOYMENT", "", "DEEPFORGE_AZURE_DEPLOYMENT", "CODEX_AZURE_DEPLOYMENT"))
    azure_api_version: str = field(default_factory=lambda: _env("AZURE_OPENAI_API_VERSION", "2025-01-01-preview", "DEEPFORGE_AZURE_API_VERSION", "CODEX_AZURE_API_VERSION"))
    azure_model: str = "gpt-4o"
    azure_context_tokens: int = 262144  # 256K — GPT-5.x default; 128000 for GPT-4o
    azure_reasoning_effort: Optional[str] = None  # "low", "medium", "high", "xhigh"

    # ── Mode & Approval ───────────────────────────────────────
    mode: Mode = Mode.AGENT
    approval_policy: ApprovalPolicy = ApprovalPolicy.SUGGEST

    # ── Workspace ─────────────────────────────────────────────
    workspace: Path = field(default_factory=Path.cwd)

    # ── Context Window ────────────────────────────────────────
    max_context_tokens: int = 1_000_000  # DeepSeek V4 1M window
    compaction_threshold: float = 0.6     # Compact at 60% usage
    prefix_cache_granularity: int = 128   # 128-token cache blocks
    max_output_tokens: int = field(default_factory=lambda: int(_env("DEEPFORGE_MAX_OUTPUT_TOKENS", "8192", "CODEX_MAX_OUTPUT_TOKENS")))

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

    # ── Config file path (set during loading) ─────────────────
    config_path: Optional[Path] = None

    @classmethod
    def from_yaml(cls, config_path: Optional[Path] = None) -> "Config":
        """
        Load configuration from YAML + environment variables.

        Args:
            config_path: Path to YAML config file. Auto-discovered if None.

        Returns:
            Config instance with merged settings.
        """
        resolved_path = config_path or _discover_config_path()
        yaml_data = _load_yaml_config(resolved_path)

        # Extract top-level keys
        backend_str = yaml_data.get("backend", "deepseek").lower()
        backend = Backend(backend_str) if backend_str in {"deepseek", "azure"} else Backend.DEEPSEEK

        mode_str = yaml_data.get("mode") or _env("DEEPFORGE_MODE", "agent", "CODEX_MODE")
        policy_str = yaml_data.get("approval_policy") or _env("DEEPFORGE_APPROVAL", "suggest", "CODEX_APPROVAL")

        # DeepSeek section
        ds = yaml_data.get("deepseek", {}) or {}
        # Azure section
        az = yaml_data.get("azure", {}) or {}

        return cls(
            backend=backend,
            config_path=resolved_path,
            # DeepSeek
            api_key=ds.get("api_key") or _env("DEEPFORGE_API_KEY", "", "DEEPSEEK_API_KEY", "CODEX_API_KEY"),
            api_base_url=ds.get("base_url") or _env("DEEPFORGE_BASE_URL", "https://api.deepseek.com/v1", "CODEX_BASE_URL"),
            model=ds.get("model") or "deepseek-chat",
            # Azure
            azure_api_key=az.get("api_key") or _env("AZURE_OPENAI_API_KEY", "", "DEEPFORGE_AZURE_API_KEY", "CODEX_AZURE_API_KEY"),
            azure_endpoint=az.get("endpoint") or _env("AZURE_OPENAI_ENDPOINT", "", "DEEPFORGE_AZURE_ENDPOINT", "CODEX_AZURE_ENDPOINT"),
            azure_deployment=az.get("deployment") or _env("AZURE_OPENAI_DEPLOYMENT", "", "DEEPFORGE_AZURE_DEPLOYMENT", "CODEX_AZURE_DEPLOYMENT"),
            azure_api_version=az.get("api_version") or _env("AZURE_OPENAI_API_VERSION", "2025-01-01-preview", "DEEPFORGE_AZURE_API_VERSION", "CODEX_AZURE_API_VERSION"),
            azure_model=az.get("model") or "gpt-4o",
            azure_context_tokens=int(az.get("context_window", 262144)),
            azure_reasoning_effort=az.get("reasoning_effort"),
            # Mode & Policy
            mode=Mode(mode_str),
            approval_policy=ApprovalPolicy(policy_str),
            # Workspace
            workspace=Path(yaml_data.get("workspace")) if yaml_data.get("workspace") else Path.cwd(),
            # Max tokens (from backend-specific config or env)
            max_output_tokens=int(
                ds.get("max_output_tokens")
                or az.get("max_output_tokens")
                or _env("DEEPFORGE_MAX_OUTPUT_TOKENS", "8192", "CODEX_MAX_OUTPUT_TOKENS")
            ),
        )

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables (no YAML)."""
        mode_str = _env("DEEPFORGE_MODE", "agent", "CODEX_MODE")
        policy_str = _env("DEEPFORGE_APPROVAL", "suggest", "CODEX_APPROVAL")
        backend_str = _env("DEEPFORGE_BACKEND", "deepseek", "CODEX_BACKEND")
        return cls(
            backend=Backend(backend_str) if backend_str in {"deepseek", "azure"} else Backend.DEEPSEEK,
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
