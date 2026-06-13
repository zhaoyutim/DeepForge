"""Tool system for DeepForge."""

from deepforge.tools.base import BaseTool, ToolRegistry, get_registry, set_registry
from deepforge.tools.file_tools import (
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    ListDirectoryTool,
)
from deepforge.tools.search_tools import (
    GrepFilesTool,
    FileSearchTool,
    WebSearchTool,
    FetchUrlTool,
)
from deepforge.tools.shell_tools import ExecShellTool
from deepforge.tools.git_tools import (
    GitStatusTool,
    GitDiffTool,
    GitLogTool,
)
from deepforge.tools.browser_tools import (
    BrowserOpenTool,
    BrowserSnapshotTool,
    BrowserClickTool,
    BrowserTypeTool,
    BrowserSelectTool,
    BrowserWaitTool,
    BrowserScreenshotTool,
    BrowserEvalTool,
    BrowserCloseTool,
    build_browser_tools,
)

__all__ = [
    "BaseTool",
    "ToolRegistry",
    "get_registry",
    "set_registry",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ListDirectoryTool",
    "GrepFilesTool",
    "FileSearchTool",
    "WebSearchTool",
    "FetchUrlTool",
    "ExecShellTool",
    "GitStatusTool",
    "GitDiffTool",
    "GitLogTool",
    "BrowserOpenTool",
    "BrowserSnapshotTool",
    "BrowserClickTool",
    "BrowserTypeTool",
    "BrowserSelectTool",
    "BrowserWaitTool",
    "BrowserScreenshotTool",
    "BrowserEvalTool",
    "BrowserCloseTool",
    "build_browser_tools",
]
