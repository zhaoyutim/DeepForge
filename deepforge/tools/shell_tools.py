"""
Shell execution tool for DeepForge.

Implements exec_shell with:
- Subprocess execution with timeout
- Working directory scoped to workspace
- Output capture (stdout + stderr)
- Environment variable passthrough
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from deepforge.config import config
from deepforge.tools.base import BaseTool
from deepforge.types import ToolCall, ToolResult


class ExecShellTool(BaseTool):
    """Execute a shell command in the workspace."""

    name = "exec_shell"
    description = (
        "Execute a shell command in the workspace. "
        "Use for system diagnostics, file operations, and development tasks."
    )
    is_read = False
    is_write = False
    is_shell = True

    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute (e.g., 'ls -la', 'python -c ...')",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the command (relative to workspace)",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Timeout in seconds (default: 30)",
            },
            "env": {
                "type": "object",
                "description": "Additional environment variables",
            },
        },
        "required": ["command"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        if not config.shell_enabled:
            return ToolResult(
                tool_call_id=tool_call.id,
                content="Error: Shell execution is disabled in the current configuration.",
                success=False,
                error="Shell disabled",
            )

        command = tool_call.arguments.get("command", "")
        cwd_arg = tool_call.arguments.get("cwd", None)
        timeout = tool_call.arguments.get("timeout_seconds", config.tool_timeout_seconds)
        extra_env = tool_call.arguments.get("env", {})

        # Resolve working directory
        workspace = Path(config.workspace)
        cwd = str(workspace)
        if cwd_arg:
            cwd_path = Path(cwd_arg)
            if not cwd_path.is_absolute():
                cwd_path = workspace / cwd_path
            if cwd_path.exists() and cwd_path.is_dir():
                cwd = str(cwd_path)
            else:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=f"Error: Working directory not found: {cwd_arg}",
                    success=False,
                    error=f"cwd not found: {cwd_arg}",
                )

        # Build environment
        env = os.environ.copy()
        env.update(extra_env)

        start_time = time.time()
        try:
            process = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            latency_ms = (time.time() - start_time) * 1000
            stdout = process.stdout.strip()
            stderr = process.stderr.strip()

            output_parts = []
            if stdout:
                output_parts.append(stdout)
            if stderr:
                output_parts.append(f"[stderr]\n{stderr}")

            output = "\n".join(output_parts) if output_parts else "(no output)"

            # Add exit code info
            prefix = ""
            if process.returncode != 0:
                prefix = f"[exit code: {process.returncode}] "

            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"{prefix}{output}\n[{(latency_ms/1000):.1f}s]",
                success=True,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: Command timed out after {timeout}s",
                success=False,
                error="Timeout",
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error executing command: {e}",
                success=False,
                error=str(e),
            )
