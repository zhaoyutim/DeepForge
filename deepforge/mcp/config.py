"""Configuration loading for MCP servers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


DEFAULT_MCP_CONFIG_NAME = "mcp.yaml"


def default_mcp_config_path() -> Path:
    """Return the default MCP config path under ~/.deepforge."""
    home = Path(os.environ.get("DEEPFORGE_HOME") or os.environ.get("CUSTOM_CODEX_HOME", "~/.deepforge")).expanduser()
    return home / DEFAULT_MCP_CONFIG_NAME


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _resolve_value(value: Any) -> str:
    """Resolve env-backed config values without expanding arbitrary shell syntax."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith("env:"):
        return os.environ.get(text[4:], "")
    if text.startswith("${") and text.endswith("}"):
        return os.environ.get(text[2:-1], "")
    if text.startswith("$") and len(text) > 1 and text[1:].replace("_", "").isalnum():
        return os.environ.get(text[1:], "")
    return text


def _resolve_mapping(values: Optional[dict[str, Any]], *, drop_empty: bool = False) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, value in (values or {}).items():
        resolved_value = _resolve_value(value)
        if drop_empty and not resolved_value:
            continue
        resolved[str(key)] = resolved_value
    return resolved


@dataclass
class MCPToolOverride:
    """Local safety metadata override for a remote MCP tool."""

    is_read: Optional[bool] = None
    is_write: Optional[bool] = None
    is_shell: Optional[bool] = None
    is_network: Optional[bool] = None
    requires_approval: Optional[bool] = None

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "MCPToolOverride":
        data = data or {}
        return cls(
            is_read=None if "is_read" not in data else _as_bool(data.get("is_read")),
            is_write=None if "is_write" not in data else _as_bool(data.get("is_write")),
            is_shell=None if "is_shell" not in data else _as_bool(data.get("is_shell")),
            is_network=None if "is_network" not in data else _as_bool(data.get("is_network")),
            requires_approval=(
                None
                if "requires_approval" not in data
                else _as_bool(data.get("requires_approval"))
            ),
        )


@dataclass
class MCPServerConfig:
    """Configuration for one MCP server."""

    name: str
    transport: str = "streamable_http"
    enabled: bool = True
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    url: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    auth_token_env: Optional[str] = None
    timeout_seconds: float = 30.0
    sse_read_timeout_seconds: float = 300.0
    retry_attempts: int = 1
    retry_backoff_seconds: float = 1.0
    tool_overrides: dict[str, MCPToolOverride] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "MCPServerConfig":
        headers = _resolve_mapping(data.get("headers"), drop_empty=True)
        auth_token_env = data.get("auth_token_env") or data.get("bearer_token_env")
        if auth_token_env and "Authorization" not in headers:
            token = os.environ.get(str(auth_token_env), "")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        overrides = {
            str(tool_name): MCPToolOverride.from_dict(tool_config)
            for tool_name, tool_config in (data.get("tool_overrides") or {}).items()
        }

        return cls(
            name=name,
            transport=str(data.get("transport", "streamable_http")).replace("-", "_"),
            enabled=_as_bool(data.get("enabled"), True),
            command=data.get("command"),
            args=_as_list(data.get("args")),
            url=data.get("url"),
            env=_resolve_mapping(data.get("env")),
            headers=headers,
            auth_token_env=str(auth_token_env) if auth_token_env else None,
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
            sse_read_timeout_seconds=float(data.get("sse_read_timeout_seconds", 300.0)),
            retry_attempts=max(1, int(data.get("retry_attempts", 1))),
            retry_backoff_seconds=float(data.get("retry_backoff_seconds", 1.0)),
            tool_overrides=overrides,
        )


@dataclass
class MCPConfig:
    """Top-level MCP configuration."""

    enabled: bool = True
    path: Path = field(default_factory=default_mcp_config_path)
    servers: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[Path] = None, *, enabled: bool = True) -> "MCPConfig":
        config_path = (path or default_mcp_config_path()).expanduser()
        if not enabled or not config_path.exists():
            return cls(enabled=False, path=config_path, servers=[])

        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load MCP config files") from exc

        with open(config_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}

        root = raw.get("mcp", raw)
        mcp_enabled = _as_bool(root.get("enabled"), True)
        servers_raw = root.get("servers") or {}
        servers: list[MCPServerConfig] = []

        if isinstance(servers_raw, dict):
            for name, server_data in servers_raw.items():
                servers.append(MCPServerConfig.from_dict(str(name), server_data or {}))
        elif isinstance(servers_raw, list):
            for index, server_data in enumerate(servers_raw):
                if not isinstance(server_data, dict):
                    continue
                name = str(server_data.get("name") or f"server_{index + 1}")
                servers.append(MCPServerConfig.from_dict(name, server_data))

        return cls(enabled=mcp_enabled and enabled, path=config_path, servers=servers)

    @property
    def active_servers(self) -> list[MCPServerConfig]:
        return [server for server in self.servers if server.enabled]