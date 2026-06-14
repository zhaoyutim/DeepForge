"""Browser computer-use tools backed by Playwright/CDP."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from deepforge.audit import get_audit_log
from deepforge.computer.browser import BrowserRuntime, get_browser_runtime
from deepforge.config import config
from deepforge.tools.base import BaseTool
from deepforge.types import ToolCall, ToolResult


class _BrowserTool(BaseTool):
    """Shared helpers for browser tools."""

    is_read = False
    is_write = False
    is_shell = False
    is_network = True
    requires_user_approval = False

    def __init__(self, runtime: Optional[BrowserRuntime] = None):
        self.runtime = runtime

    def _runtime(self) -> BrowserRuntime:
        return self.runtime or get_browser_runtime()

    def _ok(self, tool_call: ToolCall, content: str) -> ToolResult:
        return ToolResult(tool_call_id=tool_call.id, content=content, success=True)

    def _fail(self, tool_call: ToolCall, exc: Exception) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Error: {exc}",
            success=False,
            error=str(exc),
        )

    def _record(self, event_type: str, summary: str, **metadata) -> None:
        get_audit_log().record(event_type, summary, metadata)


class BrowserOpenTool(_BrowserTool):
    name = "browser_open"
    description = "Open a URL in the local Playwright-controlled browser and return a structured page snapshot."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to open. If no scheme is given, https:// is used."},
            "new_page": {"type": "boolean", "description": "Open in a new browser page instead of the current page."},
        },
        "required": ["url"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            url = str(tool_call.arguments.get("url", ""))
            snapshot = self._runtime().open(url, new_page=bool(tool_call.arguments.get("new_page", False)))
            self._record("browser_open", f"Opened {snapshot.url}", title=snapshot.title)
            return self._ok(tool_call, snapshot.to_text(max_body_chars=1600))
        except Exception as exc:
            return self._fail(tool_call, exc)


class BrowserSnapshotTool(_BrowserTool):
    name = "browser_snapshot"
    description = "Inspect the current browser page as structured text and interactive element references."
    is_read = True
    is_network = False
    parameters = {
        "type": "object",
        "properties": {
            "max_elements": {
                "type": "integer",
                "description": "Maximum interactive elements to include (default from config).",
            },
            "include_body": {
                "type": "boolean",
                "description": "Include visible page text in addition to element refs (default true).",
            },
        },
        "required": [],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            max_elements = tool_call.arguments.get("max_elements")
            snapshot = self._runtime().snapshot(max_elements=max_elements)
            include_body = tool_call.arguments.get("include_body", True)
            self._record("browser_snapshot", f"Snapshot {snapshot.url}", elements=len(snapshot.elements))
            return self._ok(tool_call, snapshot.to_text(include_body=bool(include_body)))
        except Exception as exc:
            return self._fail(tool_call, exc)


class BrowserClickTool(_BrowserTool):
    name = "browser_click"
    description = "Click an element in the browser by snapshot ref (for example e0) or CSS selector."
    parameters = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Element ref from browser_snapshot (e.g. e0) or a CSS selector.",
            },
        },
        "required": ["target"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            target = str(tool_call.arguments.get("target", ""))
            snapshot = self._runtime().click(target)
            self._record("browser_click", f"Clicked {target}", url=snapshot.url)
            return self._ok(tool_call, snapshot.to_text(max_body_chars=1200))
        except Exception as exc:
            return self._fail(tool_call, exc)


class BrowserTypeTool(_BrowserTool):
    name = "browser_type"
    description = "Type text into a browser element by snapshot ref or CSS selector."
    parameters = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Element ref (e.g. e0) or CSS selector."},
            "text": {"type": "string", "description": "Text to type."},
            "clear": {"type": "boolean", "description": "Clear the field before typing (default true)."},
            "press_enter": {"type": "boolean", "description": "Press Enter after typing."},
        },
        "required": ["target", "text"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            target = str(tool_call.arguments.get("target", ""))
            text = str(tool_call.arguments.get("text", ""))
            snapshot = self._runtime().type_text(
                target,
                text,
                clear=bool(tool_call.arguments.get("clear", True)),
                press_enter=bool(tool_call.arguments.get("press_enter", False)),
            )
            self._record("browser_type", f"Typed into {target}", chars=len(text), url=snapshot.url)
            return self._ok(tool_call, snapshot.to_text(max_body_chars=1200))
        except Exception as exc:
            return self._fail(tool_call, exc)


class BrowserSelectTool(_BrowserTool):
    name = "browser_select"
    description = "Select an option in a browser <select> element by value, label, or index."
    parameters = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Element ref (e.g. e0) or CSS selector."},
            "value": {"type": "string", "description": "Option value to select."},
            "label": {"type": "string", "description": "Option label to select."},
            "index": {"type": "integer", "description": "Zero-based option index to select."},
        },
        "required": ["target"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            target = str(tool_call.arguments.get("target", ""))
            snapshot = self._runtime().select_option(
                target,
                value=tool_call.arguments.get("value"),
                label=tool_call.arguments.get("label"),
                index=tool_call.arguments.get("index"),
            )
            self._record("browser_select", f"Selected option in {target}", url=snapshot.url)
            return self._ok(tool_call, snapshot.to_text(max_body_chars=1200))
        except Exception as exc:
            return self._fail(tool_call, exc)


class BrowserWaitTool(_BrowserTool):
    name = "browser_wait"
    description = "Wait for time, a selector, or a URL in the current browser page."
    is_read = True
    parameters = {
        "type": "object",
        "properties": {
            "milliseconds": {"type": "integer", "description": "Milliseconds to wait, capped at 60000."},
            "selector": {"type": "string", "description": "Element ref or CSS selector to wait for."},
            "url": {"type": "string", "description": "URL glob or exact URL to wait for."},
            "state": {
                "type": "string",
                "description": "Selector state to wait for: attached, detached, visible, or hidden.",
            },
        },
        "required": [],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            snapshot = self._runtime().wait(
                milliseconds=tool_call.arguments.get("milliseconds"),
                selector=tool_call.arguments.get("selector"),
                url=tool_call.arguments.get("url"),
                state=str(tool_call.arguments.get("state", "visible")),
            )
            self._record("browser_wait", f"Waited on {snapshot.url}")
            return self._ok(tool_call, snapshot.to_text(max_body_chars=1200))
        except Exception as exc:
            return self._fail(tool_call, exc)


class BrowserScreenshotTool(_BrowserTool):
    name = "browser_screenshot"
    description = "Save a screenshot of the current browser page and return the image path."
    is_read = True
    is_network = False
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional output path. Defaults to the configured browser screenshot directory.",
            },
            "full_page": {"type": "boolean", "description": "Capture the full scrollable page (default true)."},
        },
        "required": [],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            path_arg = tool_call.arguments.get("path")
            path = Path(path_arg).expanduser() if path_arg else None
            output_path = self._runtime().screenshot(
                path=path,
                full_page=bool(tool_call.arguments.get("full_page", True)),
            )
            self._record("browser_screenshot", f"Saved screenshot to {output_path}")
            return self._ok(tool_call, f"Screenshot saved: {output_path}")
        except Exception as exc:
            return self._fail(tool_call, exc)


class BrowserEvalTool(_BrowserTool):
    name = "browser_eval"
    description = "Evaluate JavaScript in the current page. Use only when structured browser tools are insufficient."
    requires_user_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "script": {"type": "string", "description": "JavaScript expression or function body to evaluate."},
        },
        "required": ["script"],
    }

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            result = self._runtime().evaluate(str(tool_call.arguments.get("script", "")))
            self._record("browser_eval", "Evaluated JavaScript in browser")
            return self._ok(tool_call, result)
        except Exception as exc:
            return self._fail(tool_call, exc)


class BrowserCloseTool(_BrowserTool):
    name = "browser_close"
    description = "Close the Playwright-controlled browser session."
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            self._runtime().close()
            self._record("browser_close", "Closed browser runtime")
            return self._ok(tool_call, "Browser closed.")
        except Exception as exc:
            return self._fail(tool_call, exc)


def build_browser_tools(runtime: Optional[BrowserRuntime] = None) -> list[BaseTool]:
    """Return browser computer-use tools when enabled in config."""
    if not config.browser_enabled:
        return []
    return [
        BrowserOpenTool(runtime),
        BrowserSnapshotTool(runtime),
        BrowserClickTool(runtime),
        BrowserTypeTool(runtime),
        BrowserSelectTool(runtime),
        BrowserWaitTool(runtime),
        BrowserScreenshotTool(runtime),
        BrowserEvalTool(runtime),
        BrowserCloseTool(runtime),
    ]