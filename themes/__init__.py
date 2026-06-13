"""
Theme system for DeepForge TUI.

Each theme is a module in this directory that exports a `theme` instance.
Themes provide colors, banner rendering, and status bar rendering.

To add a new theme:
  1. Create a new .py file in this directory
  2. Define a Theme instance named `theme`
  3. It will be auto-discovered
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from rich.panel import Panel
from rich.text import Text


@dataclass
class Theme:
    """A TUI visual theme.

    name          — unique identifier (matches module filename)
    label         — human-readable name shown in /theme list
    colors        — dict of color style strings (e.g. {"banner": "bold cyan", ...})
    render_banner — (session) -> Panel
    render_status_bar — (session) -> Text
    render_dashboard — optional (session) -> list[Rich renderables]  shown on /theme X
    """

    name: str
    label: str
    colors: dict[str, str]
    render_banner: Callable
    render_status_bar: Callable
    render_dashboard: Optional[Callable] = None


# ── Registry ──────────────────────────────────────────────────────────

_themes: dict[str, Theme] = {}
_active_theme: Optional[Theme] = None


def register(theme: Theme) -> None:
    """Register a theme for auto-discovery."""
    if theme.name in _themes:
        raise ValueError(f"Theme '{theme.name}' is already registered")
    _themes[theme.name] = theme


def get(name: str) -> Optional[Theme]:
    """Look up a theme by name."""
    return _themes.get(name)


def list_themes() -> dict[str, Theme]:
    """Return all registered themes."""
    return dict(_themes)


def get_active() -> Optional[Theme]:
    """Return the currently active theme."""
    return _active_theme


def activate(name: str) -> Theme:
    """Activate a theme by name.  Returns the activated Theme."""
    theme = _themes.get(name)
    if theme is None:
        available = ", ".join(_themes.keys())
        raise ValueError(f"Theme '{name}' not found. Available: {available}")
    global _active_theme
    _active_theme = theme
    return theme


def _auto_discover() -> None:
    """Import all theme modules in this directory to auto-register."""
    import importlib
    import pkgutil

    package_dir = Path(__file__).parent
    for finder, module_name, is_pkg in pkgutil.iter_modules([str(package_dir)]):
        if module_name.startswith("_"):
            continue
        try:
            importlib.import_module(f"themes.{module_name}")
        except Exception:
            pass  # Skip broken themes gracefully


# Auto-discover on first import
_auto_discover()
