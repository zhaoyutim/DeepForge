"""Local environment diagnostics for DeepForge."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from deepforge.config import config
from deepforge.mcp.config import default_mcp_config_path


@dataclass
class DoctorCheck:
    name: str
    status: str
    detail: str


REQUIRED_MODULES = ["openai", "tiktoken", "httpx", "rich", "pydantic", "yaml", "mcp"]
DEV_MODULES = ["pytest", "ruff"]


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run_doctor(*, workspace: Optional[Path] = None) -> list[DoctorCheck]:
    workspace = Path(workspace or config.workspace).expanduser().resolve()
    checks: list[DoctorCheck] = []

    checks.append(DoctorCheck("Python", "ok", sys.version.split()[0]))
    checks.append(DoctorCheck(
        "Workspace",
        "ok" if workspace.exists() and workspace.is_dir() else "fail",
        str(workspace),
    ))

    api_key_present = bool(
        os.environ.get("DEEPFORGE_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("CODEX_API_KEY")
    )
    checks.append(DoctorCheck(
        "API key",
        "ok" if api_key_present else "warn",
        "configured" if api_key_present else "missing DEEPFORGE_API_KEY/DEEPSEEK_API_KEY",
    ))

    for module_name in REQUIRED_MODULES:
        checks.append(DoctorCheck(
            f"module:{module_name}",
            "ok" if _module_available(module_name) else "fail",
            "installed" if _module_available(module_name) else "missing",
        ))

    for module_name in DEV_MODULES:
        checks.append(DoctorCheck(
            f"dev:{module_name}",
            "ok" if _module_available(module_name) else "warn",
            "installed" if _module_available(module_name) else "missing; install with pip install -e '.[dev]'",
        ))

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=5,
        )
        checks.append(DoctorCheck(
            "Git",
            "ok" if result.returncode == 0 else "warn",
            "repository" if result.returncode == 0 else (result.stderr.strip() or "not a git repository"),
        ))
    except Exception as exc:
        checks.append(DoctorCheck("Git", "warn", str(exc)))

    project_config = config.project_config_path or workspace / ".deepforge.yaml"
    checks.append(DoctorCheck(
        "Project config",
        "ok" if Path(project_config).expanduser().exists() else "warn",
        str(project_config) if Path(project_config).expanduser().exists() else "not found; optional .deepforge.yaml",
    ))

    mcp_path = config.mcp_config_path or default_mcp_config_path()
    checks.append(DoctorCheck(
        "MCP config",
        "ok" if Path(mcp_path).expanduser().exists() else "warn",
        str(mcp_path) if Path(mcp_path).expanduser().exists() else "not found; MCP disabled unless configured",
    ))

    playwright_ok = _module_available("playwright")
    checks.append(DoctorCheck(
        "Playwright",
        "ok" if playwright_ok else "warn",
        "installed" if playwright_ok else "optional; install with pip install -e '.[browser]'",
    ))

    checks.append(DoctorCheck(
        "Session logs",
        "ok" if config.session_log_enabled else "warn",
        str(Path(config.session_log_dir).expanduser()) if config.session_log_enabled else "disabled",
    ))
    return checks


def format_doctor(checks: list[DoctorCheck]) -> str:
    width = max((len(check.name) for check in checks), default=10)
    lines = ["DeepForge doctor"]
    for check in checks:
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(check.status, check.status.upper())
        lines.append(f"{marker:>4}  {check.name:<{width}}  {check.detail}")
    failures = sum(1 for check in checks if check.status == "fail")
    warnings = sum(1 for check in checks if check.status == "warn")
    lines.append(f"Summary: {failures} failure(s), {warnings} warning(s)")
    return "\n".join(lines)
