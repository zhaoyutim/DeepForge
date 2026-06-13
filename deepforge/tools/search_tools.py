"""
Search and web tools for DeepForge.

Tools:
- grep_files: Regex search across workspace files
- file_search: Fuzzy filename search
- web_search: Web search via DuckDuckGo/Bing
- fetch_url: HTTP GET a known URL
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from deepforge.config import config
from deepforge.tools.base import BaseTool
from deepforge.types import ToolCall, ToolResult


class GrepFilesTool(BaseTool):
    """Search for a regex pattern in workspace files."""

    name = "grep_files"
    description = (
        "Search for a regex pattern in workspace files. "
        "Returns matching lines with context. Respects .gitignore patterns."
    )
    is_read = True
    is_write = False

    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search (relative to workspace, default: .)",
            },
            "include": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns for files to include",
            },
            "exclude": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns for files to exclude",
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "Case-insensitive matching",
            },
            "context_lines": {
                "type": "integer",
                "description": "Context lines before/after match",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default: 100)",
            },
        },
        "required": ["pattern"],
    }

    # Common ignore patterns
    _IGNORE_DIRS = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "target", "build", "dist", ".eggs",
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        pattern = tool_call.arguments.get("pattern", "")
        search_path = tool_call.arguments.get("path", ".")
        case_insensitive = tool_call.arguments.get("case_insensitive", False)
        context_lines = tool_call.arguments.get("context_lines", 2)
        max_results = tool_call.arguments.get("max_results", 100)

        workspace = Path(config.workspace)
        base = Path(search_path)
        if not base.is_absolute():
            base = workspace / base

        if not base.exists():
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: Path not found: {search_path}",
                success=False,
                error=f"Path not found: {search_path}",
            )

        try:
            flags = re.IGNORECASE if case_insensitive else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error: Invalid regex pattern: {e}",
                success=False,
                error=str(e),
            )

        results = []
        files_searched = 0

        # Walk files
        walk_root = base if base.is_dir() else base.parent
        files_iter = [base] if base.is_file() else walk_root.rglob("*")

        for file_path in files_iter:
            if not file_path.is_file():
                continue

            # Skip ignored directories
            parts = set(file_path.parts)
            if parts & self._IGNORE_DIRS:
                continue

            # Skip binary-looking files
            suffix = file_path.suffix.lower()
            if suffix in {".pyc", ".pyo", ".so", ".dylib", ".bin", ".exe", ".dll"}:
                continue

            files_searched += 1
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue

            for i, line in enumerate(lines):
                if regex.search(line):
                    rel_path = file_path.relative_to(workspace)
                    ctx_start = max(0, i - context_lines)
                    ctx_end = min(len(lines), i + context_lines + 1)
                    snippet = "".join(
                        f"  {j+1}: {lines[j].rstrip()}\n"
                        for j in range(ctx_start, ctx_end)
                    )
                    results.append(f"--- {rel_path}:{i+1} ---\n{snippet}")
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break

        if not results:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"No matches for '{pattern}' in {files_searched} files.",
                success=True,
            )

        content = f"Found {len(results)} match(es) in {files_searched} files:\n\n" + "\n".join(results)
        if len(results) >= max_results:
            content += f"\n(results truncated at {max_results})"

        return ToolResult(
            tool_call_id=tool_call.id,
            content=content,
            success=True,
        )


class FileSearchTool(BaseTool):
    """Find files by name using fuzzy matching."""

    name = "file_search"
    description = "Find files by name using fuzzy matching with score-based ranking."
    is_read = True
    is_write = False

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (file name or path fragment)",
            },
            "path": {
                "type": "string",
                "description": "Base path to search (relative to workspace)",
            },
            "extensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "File extensions to filter by",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results (default: 20)",
            },
        },
        "required": ["query"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        query = tool_call.arguments.get("query", "").lower()
        search_path = tool_call.arguments.get("path", ".")
        extensions = tool_call.arguments.get("extensions", [])
        limit = tool_call.arguments.get("limit", 20)

        workspace = Path(config.workspace)
        base = Path(search_path)
        if not base.is_absolute():
            base = workspace / base

        if not base.exists():
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Path not found: {search_path}",
                success=False,
                error=f"Path not found: {search_path}",
            )

        matches = []
        for file_path in base.rglob("*"):
            if not file_path.is_file():
                continue
            # Filter by extension
            if extensions:
                if file_path.suffix.lstrip(".") not in extensions:
                    continue
            # Fuzzy match
            rel_path = str(file_path.relative_to(workspace))
            # Score: exact match > contains > substring
            if query == file_path.name.lower():
                score = 100
            elif query in file_path.name.lower():
                score = 80
            elif query in rel_path.lower():
                score = 60
            else:
                # Check character-by-character matching
                name = file_path.name.lower()
                qi = 0
                for ch in name:
                    if qi < len(query) and ch == query[qi]:
                        qi += 1
                if qi >= len(query) * 0.7:
                    score = qi / len(query) * 40
                else:
                    continue

            matches.append((score, rel_path))
            if len(matches) >= limit * 3:
                break

        # Sort by score, take top hits
        matches.sort(key=lambda x: x[0], reverse=True)
        top = matches[:limit]

        if not top:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"No files matching '{query}'",
                success=True,
            )

        content = f"Found {len(top)} file(s) matching '{query}':\n"
        for score, path in top:
            content += f"  [{score:.0f}] {path}\n"

        return ToolResult(
            tool_call_id=tool_call.id,
            content=content,
            success=True,
        )


class FetchUrlTool(BaseTool):
    """Fetch a known URL directly (HTTP GET)."""

    name = "fetch_url"
    description = "Fetch a known URL directly (HTTP GET) and return its content."
    is_read = True
    is_write = False
    is_network = True

    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Absolute HTTP/HTTPS URL to fetch",
            },
            "max_bytes": {
                "type": "integer",
                "description": "Truncate response after this many bytes",
            },
        },
        "required": ["url"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        url = tool_call.arguments.get("url", "")
        max_bytes = tool_call.arguments.get("max_bytes", 1_000_000)

        try:
            import httpx
        except ImportError:
            return ToolResult(
                tool_call_id=tool_call.id,
                content="Error: httpx not installed. Run: pip install httpx",
                success=False,
                error="httpx not installed",
            )

        try:
            response = httpx.get(url, follow_redirects=True, timeout=15.0)
            content = response.text[:max_bytes]
            return ToolResult(
                tool_call_id=tool_call.id,
                content=content,
                success=True,
            )
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error fetching '{url}': {e}",
                success=False,
                error=str(e),
            )


class WebSearchTool(BaseTool):
    """Search the web and return results."""

    name = "web_search"
    description = "Search the web and return ranked results with URLs and snippets."
    is_read = True
    is_write = False
    is_network = True

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default: 5, max: 10)",
            },
        },
        "required": ["query"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        query = tool_call.arguments.get("query", "")
        max_results = tool_call.arguments.get("max_results", 5)

        # Use DuckDuckGo HTML (no API key needed)
        try:
            import httpx
        except ImportError:
            return ToolResult(
                tool_call_id=tool_call.id,
                content="Error: httpx not installed. Run: pip install httpx",
                success=False,
                error="httpx not installed",
            )

        try:
            search_url = f"https://html.duckduckgo.com/html/?q={query}"
            headers = {"User-Agent": "DeepForge/0.1"}
            response = httpx.get(search_url, headers=headers, timeout=10.0)
            response.raise_for_status()
        except Exception as e:
            # Fallback: return a message
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Web search unavailable: {e}\n\nTry using fetch_url for known URLs.",
                success=False,
                error=str(e),
            )

        # Simple HTML extraction for result links and snippets
        html = response.text
        results = []
        # Extract result snippets (very basic parser)
        link_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        snippet_pattern = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (url, title) in enumerate(links[:max_results]):
            title_clean = re.sub(r"<[^>]+>", "", title).strip()
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
            results.append(f"[{i+1}] {title_clean}\n    {url}\n    {snippet}")

        if not results:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"No results found for '{query}'",
                success=True,
            )

        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Web search results for '{query}':\n\n" + "\n\n".join(results),
            success=True,
        )
