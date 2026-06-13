"""
Git tools for DeepForge.

Tools:
- git_status: Show working tree status
- git_diff: Show changes
- git_log: Show commit history
"""

from __future__ import annotations

from pathlib import Path

from deepforge.config import config
from deepforge.tools.base import BaseTool
from deepforge.types import ToolCall, ToolResult


class GitStatusTool(BaseTool):
    """Show the working tree status."""

    name = "git_status"
    description = "Show the working tree status (git status --porcelain)."
    is_read = True
    is_write = False

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Subdirectory or file to scope the status to",
            },
        },
        "required": [],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            from git import Repo, InvalidGitRepositoryError
        except ImportError:
            # Fallback to subprocess
            import subprocess
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain=v1", "-b"],
                    cwd=config.workspace,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=result.stdout.strip() or "(clean working tree)",
                    success=True,
                )
            except Exception as e:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=f"Error: {e}",
                    success=False,
                    error=str(e),
                )

        try:
            repo = Repo(config.workspace, search_parent_directories=True)
        except InvalidGitRepositoryError:
            return ToolResult(
                tool_call_id=tool_call.id,
                content="Not a git repository.",
                success=False,
                error="Not a git repository",
            )

        try:
            # Get branch info
            active_branch = repo.active_branch.name
            # Get status
            changed = [item.a_path for item in repo.index.diff(None)]
            untracked = repo.untracked_files
            staged = [item.a_path for item in repo.index.diff("HEAD")]

            lines = [f"## {active_branch}"]
            if staged:
                lines.append("\nStaged:")
                for f in staged:
                    lines.append(f"  M {f}")
            if changed:
                lines.append("\nModified:")
                for f in changed:
                    lines.append(f"  M {f}")
            if untracked:
                lines.append("\nUntracked:")
                for f in untracked:
                    lines.append(f"  ? {f}")
            if not (staged or changed or untracked):
                lines.append("(clean)")

            return ToolResult(
                tool_call_id=tool_call.id,
                content="\n".join(lines),
                success=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: {e}",
                success=False,
                error=str(e),
            )


class GitDiffTool(BaseTool):
    """Show changes in the working tree."""

    name = "git_diff"
    description = "Show changes in the working tree (git diff)."
    is_read = True
    is_write = False

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Subdirectory or file to scope the diff to",
            },
            "cached": {
                "type": "boolean",
                "description": "Show staged changes (--cached)",
            },
            "unified": {
                "type": "integer",
                "description": "Number of context lines (default: 3)",
            },
        },
        "required": [],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        import subprocess

        scope = tool_call.arguments.get("path", "")
        cached = tool_call.arguments.get("cached", False)
        context = tool_call.arguments.get("unified", 3)

        cmd = ["git", "diff", f"-U{context}"]
        if cached:
            cmd.append("--cached")
        if scope:
            cmd.append(scope)

        try:
            result = subprocess.run(
                cmd,
                cwd=config.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stdout.strip()
            if not output:
                output = "(no changes)"
            return ToolResult(
                tool_call_id=tool_call.id,
                content=output,
                success=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: {e}",
                success=False,
                error=str(e),
            )


class GitLogTool(BaseTool):
    """Show commit history."""

    name = "git_log"
    description = "Show commit history (git log)."
    is_read = True
    is_write = False

    parameters = {
        "type": "object",
        "properties": {
            "max_count": {
                "type": "integer",
                "description": "Maximum commits to show (default: 20)",
            },
            "path": {
                "type": "string",
                "description": "Subdirectory or file to scope history to",
            },
            "author": {
                "type": "string",
                "description": "Filter by author",
            },
            "since": {
                "type": "string",
                "description": "Lower date bound (e.g., '2 weeks ago')",
            },
        },
        "required": [],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        import subprocess

        max_count = tool_call.arguments.get("max_count", 20)
        scope = tool_call.arguments.get("path", "")
        author = tool_call.arguments.get("author", "")
        since = tool_call.arguments.get("since", "")

        cmd = [
            "git", "log",
            "--oneline",
            "--decorate",
            f"-n{max_count}",
        ]
        if author:
            cmd.append(f"--author={author}")
        if since:
            cmd.append(f"--since={since}")
        if scope:
            cmd.append("--")
            cmd.append(scope)

        try:
            result = subprocess.run(
                cmd,
                cwd=config.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stdout.strip()
            if not output:
                output = "(no commits)"
            return ToolResult(
                tool_call_id=tool_call.id,
                content=output,
                success=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: {e}",
                success=False,
                error=str(e),
            )
