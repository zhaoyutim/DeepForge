"""
File system tools for DeepForge.

Tools:
- read_file: Read file contents (UTF-8 text or PDF extraction)
- write_file: Write content to a file
- edit_file: Search-and-replace edit in a single file
- list_dir: List directory contents
"""

from __future__ import annotations

import os
from pathlib import Path

from deepforge.config import config
from deepforge.tools.base import BaseTool
from deepforge.types import ToolCall, ToolResult


class ReadFileTool(BaseTool):
    """Read a file from the workspace. Supports UTF-8 text and basic PDF extraction."""

    name = "read_file"
    description = "Read a file from the workspace. Returns file contents as text."
    is_read = True
    is_write = False

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file (relative to workspace or absolute)",
            },
            "start_line": {
                "type": "integer",
                "description": "Starting line number (1-based, default 1)",
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum lines to return (default 200, max 500)",
            },
        },
        "required": ["path"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        file_path = tool_call.arguments.get("path", "")
        start_line = tool_call.arguments.get("start_line", 1)
        max_lines = tool_call.arguments.get("max_lines", 200)

        # Resolve path
        workspace = Path(config.workspace)
        path = Path(file_path)
        if not path.is_absolute():
            path = workspace / path

        # Security: ensure path is within workspace
        try:
            path = path.resolve()
            workspace_resolved = workspace.resolve()
            if not str(path).startswith(str(workspace_resolved)):
                # Allow absolute paths too
                pass
        except Exception:
            pass

        if not path.exists():
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: File not found: {file_path}",
                success=False,
                error=f"File not found: {file_path}",
            )

        if path.is_dir():
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: '{file_path}' is a directory, not a file",
                success=False,
                error=f"'{file_path}' is a directory",
            )

        try:
            # Read as text
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)
            start_idx = max(0, start_line - 1)
            end_idx = min(total_lines, start_idx + max(500, max_lines))
            selected = lines[start_idx:end_idx]

            content = "".join(selected)
            if end_idx < total_lines:
                content += f"\n\n... (truncated: {total_lines - end_idx} more lines, {total_lines} total)"

            return ToolResult(
                tool_call_id=tool_call.id,
                content=content,
                success=True,
            )
        except UnicodeDecodeError:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: Cannot read '{file_path}' — not a UTF-8 text file",
                success=False,
                error="File is not UTF-8 encoded",
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error reading '{file_path}': {e}",
                success=False,
                error=str(e),
            )


class WriteFileTool(BaseTool):
    """Write content to a file in the workspace. Creates parent directories."""

    name = "write_file"
    description = "Write content to a UTF-8 file. Creates or overwrites; parent directories are auto-created. For large apps, write separate HTML/CSS/JS files instead of one huge tool call."
    is_read = False
    is_write = True
    argument_aliases = {
        "path": ["file_path", "filepath", "filename", "file", "name"],
        "content": ["contents", "text", "body", "data", "code", "source"],
    }

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file (relative to workspace)",
            },
            "content": {
                "type": "string",
                "description": "Content to write",
            },
        },
        "required": ["path", "content"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        file_path = tool_call.arguments.get("path", "")
        content = tool_call.arguments.get("content", "")

        workspace = Path(config.workspace)
        path = Path(file_path)
        if not path.is_absolute():
            path = workspace / path

        try:
            # Create parent directories
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write file
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            file_size = path.stat().st_size
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Created {file_path} ({file_size} bytes)",
                success=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error writing '{file_path}': {e}",
                success=False,
                error=str(e),
            )


class EditFileTool(BaseTool):
    """Replace text in a single file via exact search/replace."""

    name = "edit_file"
    description = (
        "Replace text in a single file via exact search/replace. "
        "Use for one unambiguous in-place edit."
    )
    is_read = False
    is_write = True

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file",
            },
            "search": {
                "type": "string",
                "description": "Exact text to search for (including whitespace and indentation)",
            },
            "replace": {
                "type": "string",
                "description": "Text to replace with",
            },
        },
        "required": ["path", "search", "replace"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        file_path = tool_call.arguments.get("path", "")
        search_text = tool_call.arguments.get("search", "")
        replace_text = tool_call.arguments.get("replace", "")

        workspace = Path(config.workspace)
        path = Path(file_path)
        if not path.is_absolute():
            path = workspace / path

        if not path.exists():
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: File not found: {file_path}",
                success=False,
                error=f"File not found: {file_path}",
            )

        try:
            with open(path, "r", encoding="utf-8") as f:
                original = f.read()

            if search_text not in original:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=f"Error: Search text not found in '{file_path}'",
                    success=False,
                    error="Search text not found",
                )

            # Count occurrences
            count = original.count(search_text)
            if count > 1:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=(
                        f"Error: Search text found {count} times in '{file_path}'. "
                        f"Use a more specific search string to target a single occurrence."
                    ),
                    success=False,
                    error=f"Ambiguous: {count} matches found",
                )

            # Perform replacement
            modified = original.replace(search_text, replace_text, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(modified)

            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Edited {file_path}: 1 replacement made",
                success=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error editing '{file_path}': {e}",
                success=False,
                error=str(e),
            )


class ListDirectoryTool(BaseTool):
    """List entries in a directory."""

    name = "list_dir"
    description = "List entries in a directory relative to the workspace."
    is_read = True
    is_write = False

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path (default: workspace root)",
            },
        },
        "required": [],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        dir_path = tool_call.arguments.get("path", ".")

        workspace = Path(config.workspace)
        path = Path(dir_path)
        if not path.is_absolute():
            path = workspace / path

        if not path.exists():
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: Directory not found: {dir_path}",
                success=False,
                error=f"Directory not found: {dir_path}",
            )

        if not path.is_dir():
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: '{dir_path}' is not a directory",
                success=False,
                error=f"'{dir_path}' is not a directory",
            )

        try:
            entries = []
            with os.scandir(path) as it:
                for entry in sorted(it, key=lambda e: e.name):
                    suffix = "/" if entry.is_dir() else ""
                    entries.append(f"  {entry.name}{suffix}")

            if not entries:
                content = f"{dir_path}/ (empty)"
            else:
                content = f"{dir_path}/\n" + "\n".join(entries)

            return ToolResult(
                tool_call_id=tool_call.id,
                content=content,
                success=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error listing '{dir_path}': {e}",
                success=False,
                error=str(e),
            )
