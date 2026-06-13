"""Playwright-backed browser computer-use runtime.

This module keeps Playwright optional and loads it only when a browser tool is
actually executed. The agent talks to this runtime through structured actions
instead of raw screen coordinates.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from deepforge.config import config


class BrowserRuntimeError(RuntimeError):
    """Raised when browser computer use cannot complete an action."""


def _compact(value: Any, max_chars: int = 300) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15] + "... (truncated)"


@dataclass
class BrowserElement:
    """One visible page element surfaced to the model."""

    ref: str
    tag: str
    role: str = ""
    name: str = ""
    text: str = ""
    selector: str = ""
    element_type: str = ""
    href: str = ""
    placeholder: str = ""
    disabled: bool = False
    rect: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BrowserElement":
        return cls(
            ref=str(data.get("ref", "")),
            tag=str(data.get("tag", "")),
            role=str(data.get("role", "")),
            name=_compact(data.get("name", ""), 220),
            text=_compact(data.get("text", ""), 220),
            selector=str(data.get("selector", "")),
            element_type=str(data.get("type", "")),
            href=_compact(data.get("href", ""), 220),
            placeholder=_compact(data.get("placeholder", ""), 120),
            disabled=bool(data.get("disabled", False)),
            rect=data.get("rect", {}) if isinstance(data.get("rect"), dict) else {},
        )

    def to_text(self) -> str:
        parts = [f"ref={self.ref}", self.tag]
        if self.role:
            parts.append(f"role={self.role}")
        label = self.name or self.text or self.placeholder or self.href
        if label:
            parts.append(f"label={label!r}")
        if self.element_type:
            parts.append(f"type={self.element_type}")
        if self.disabled:
            parts.append("disabled=true")
        return " ".join(parts)


@dataclass
class BrowserSnapshot:
    """Structured page observation returned by the browser runtime."""

    url: str
    title: str
    body_text: str = ""
    elements: list[BrowserElement] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BrowserSnapshot":
        elements = [
            BrowserElement.from_dict(item)
            for item in data.get("elements", [])
            if isinstance(item, dict)
        ]
        return cls(
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            body_text=_compact(data.get("bodyText", ""), 4000),
            elements=elements,
        )

    def to_text(self, *, include_body: bool = True, max_body_chars: int = 2000) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title or '(untitled)'}"]
        if include_body and self.body_text:
            lines.extend(["", "Page text:", _compact(self.body_text, max_body_chars)])
        lines.extend(["", f"Interactive elements ({len(self.elements)}):"])
        if self.elements:
            lines.extend(f"- {element.to_text()}" for element in self.elements)
        else:
            lines.append("(none found)")
        return "\n".join(lines)


class BrowserRuntime:
    """Synchronous Playwright browser runtime used by DeepForge tools."""

    def __init__(
        self,
        *,
        headless: Optional[bool] = None,
        profile_dir: Optional[Path] = None,
        timeout_seconds: Optional[int] = None,
    ):
        self.headless = config.browser_headless if headless is None else headless
        self.profile_dir = Path(profile_dir or config.browser_profile_dir).expanduser()
        self.timeout_ms = int((timeout_seconds or config.browser_default_timeout_seconds) * 1000)
        self._playwright = None
        self._context = None
        self._page = None

    # ── Lifecycle ──────────────────────────────────────────────

    def _ensure_playwright(self):
        if self._playwright is not None:
            return self._playwright
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserRuntimeError(
                "Browser computer use requires Playwright. Install it with "
                "`pip install -e .[browser]` and run `python -m playwright install chromium`."
            ) from exc
        self._playwright_error = PlaywrightError
        self._playwright_timeout_error = PlaywrightTimeoutError
        self._playwright = sync_playwright().start()
        return self._playwright

    def _ensure_context(self):
        if self._context is not None:
            return self._context
        playwright = self._ensure_playwright()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                accept_downloads=True,
                viewport={"width": 1280, "height": 900},
            )
        except Exception as exc:
            raise BrowserRuntimeError(
                "Could not launch Chromium. If Playwright is installed, run "
                "`python -m playwright install chromium`."
            ) from exc
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        return self._context

    def _current_page(self):
        self._ensure_context()
        if self._page is None or self._page.is_closed():
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
        return self._page

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
            self._page = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    # ── Actions ────────────────────────────────────────────────

    def open(self, url: str, *, new_page: bool = False) -> BrowserSnapshot:
        page = self._current_page()
        if new_page:
            page = self._context.new_page()
            page.set_default_timeout(self.timeout_ms)
            self._page = page
        target = self._normalize_url(url)
        page.goto(target, wait_until="domcontentloaded", timeout=self.timeout_ms)
        with self._ignore_playwright_errors():
            page.wait_for_load_state("networkidle", timeout=min(self.timeout_ms, 5000))
        return self.snapshot()

    def snapshot(self, *, max_elements: Optional[int] = None) -> BrowserSnapshot:
        page = self._current_page()
        max_count = max_elements or config.browser_max_snapshot_elements
        data = page.evaluate(_SNAPSHOT_SCRIPT, max_count)
        return BrowserSnapshot.from_dict(data)

    def click(self, target: str) -> BrowserSnapshot:
        page = self._current_page()
        locator = page.locator(self._selector(target)).first
        locator.click(timeout=self.timeout_ms)
        with self._ignore_playwright_errors():
            page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 5000))
        return self.snapshot()

    def type_text(
        self,
        target: str,
        text: str,
        *,
        clear: bool = True,
        press_enter: bool = False,
    ) -> BrowserSnapshot:
        page = self._current_page()
        locator = page.locator(self._selector(target)).first
        if clear:
            locator.fill(text, timeout=self.timeout_ms)
        else:
            locator.click(timeout=self.timeout_ms)
            locator.type(text, timeout=self.timeout_ms)
        if press_enter:
            locator.press("Enter", timeout=self.timeout_ms)
            with self._ignore_playwright_errors():
                page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 5000))
        return self.snapshot()

    def select_option(
        self,
        target: str,
        *,
        value: Optional[str] = None,
        label: Optional[str] = None,
        index: Optional[int] = None,
    ) -> BrowserSnapshot:
        option: dict[str, Any] = {}
        if value is not None:
            option["value"] = value
        if label is not None:
            option["label"] = label
        if index is not None:
            option["index"] = index
        if not option:
            raise BrowserRuntimeError("select_option requires one of value, label, or index")
        page = self._current_page()
        page.locator(self._selector(target)).first.select_option(option, timeout=self.timeout_ms)
        return self.snapshot()

    def wait(
        self,
        *,
        milliseconds: Optional[int] = None,
        selector: Optional[str] = None,
        url: Optional[str] = None,
        state: str = "visible",
    ) -> BrowserSnapshot:
        page = self._current_page()
        if milliseconds is not None:
            page.wait_for_timeout(max(0, min(int(milliseconds), 60_000)))
        if selector:
            page.locator(self._selector(selector)).first.wait_for(state=state, timeout=self.timeout_ms)
        if url:
            page.wait_for_url(url, timeout=self.timeout_ms)
        return self.snapshot()

    def evaluate(self, script: str) -> str:
        page = self._current_page()
        result = page.evaluate(script)
        try:
            text = json.dumps(result, ensure_ascii=False, indent=2)
        except TypeError:
            text = str(result)
        return _compact(text, 4000)

    def screenshot(self, *, path: Optional[Path] = None, full_page: bool = True) -> Path:
        page = self._current_page()
        output_path = Path(path).expanduser() if path else self._default_screenshot_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output_path), full_page=full_page)
        return output_path

    def status(self) -> dict[str, Any]:
        page = None
        if self._context is not None and self._page is not None and not self._page.is_closed():
            page = self._page
        return {
            "started": self._context is not None,
            "headless": self.headless,
            "profile_dir": str(self.profile_dir),
            "url": page.url if page else None,
            "title": page.title() if page else None,
        }

    # ── Helpers ────────────────────────────────────────────────

    def _selector(self, target: str) -> str:
        value = str(target).strip()
        if re.fullmatch(r"e\d+", value):
            return f'[data-deepforge-ref="{value}"]'
        return value

    def _normalize_url(self, url: str) -> str:
        text = str(url).strip()
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", text):
            return text
        return f"https://{text}"

    def _default_screenshot_path(self) -> Path:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return Path(config.browser_screenshot_dir).expanduser() / f"browser-{timestamp}.png"

    def _ignore_playwright_errors(self):
        class _Ignore:
            def __init__(self, runtime: BrowserRuntime):
                self.runtime = runtime

            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                if exc_type is None:
                    return False
                ignored = tuple(
                    item
                    for item in (
                        getattr(self.runtime, "_playwright_error", None),
                        getattr(self.runtime, "_playwright_timeout_error", None),
                    )
                    if item is not None
                )
                return bool(ignored and issubclass(exc_type, ignored))

        return _Ignore(self)


_SNAPSHOT_SCRIPT = r"""
(maxElements) => {
  const compact = (value, max = 220) => String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, max);
  const visible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const roleFor = (element, tag) => {
    const explicit = element.getAttribute('role');
    if (explicit) return explicit;
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'submit' || type === 'button') return 'button';
      return 'textbox';
    }
    return '';
  };
  const nameFor = (element) => compact(
    element.getAttribute('aria-label') ||
    element.getAttribute('alt') ||
    element.getAttribute('title') ||
    element.getAttribute('placeholder') ||
    element.innerText ||
    element.value ||
    element.getAttribute('href') ||
    ''
  );
  const nodes = Array.from(document.querySelectorAll(
    'a,button,input,textarea,select,summary,label,[role],[aria-label],[contenteditable="true"]'
  )).filter(visible).slice(0, maxElements);
  const elements = nodes.map((element, index) => {
    const ref = `e${index}`;
    element.setAttribute('data-deepforge-ref', ref);
    const rect = element.getBoundingClientRect();
    const tag = element.tagName.toLowerCase();
    return {
      ref,
      tag,
      role: roleFor(element, tag),
      name: nameFor(element),
      text: compact(element.innerText || element.value || '', 220),
      type: element.getAttribute('type') || '',
      href: element.getAttribute('href') || '',
      placeholder: element.getAttribute('placeholder') || '',
      selector: `[data-deepforge-ref="${ref}"]`,
      disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
      rect: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height)
      }
    };
  });
  return {
    url: window.location.href,
    title: document.title,
    bodyText: compact(document.body ? document.body.innerText : '', 4000),
    elements
  };
}
"""


_browser_runtime: Optional[BrowserRuntime] = None


def get_browser_runtime() -> BrowserRuntime:
    """Return the process-wide browser runtime."""
    global _browser_runtime
    if _browser_runtime is None:
        _browser_runtime = BrowserRuntime()
    return _browser_runtime


def close_browser_runtime() -> None:
    """Close the process-wide browser runtime if it was started."""
    global _browser_runtime
    if _browser_runtime is not None:
        _browser_runtime.close()
        _browser_runtime = None