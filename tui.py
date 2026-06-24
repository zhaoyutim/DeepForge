#!/usr/bin/env python3
"""
DeepForge TUI — Rich terminal user interface.

Features:
- Pluggable theme system (themes/ package)
- Color-coded panels and status bars
- Live streaming response display
- Tool execution progress indicators
- Context pressure gauge
- Command history with readline
- Mode/policy/workspace status bar

Usage:
    python tui.py                        # Interactive TUI
    python tui.py --mode yolo            # YOLO mode
    python tui.py --theme worldcup        # World Cup theme
    /theme list                           # Show available themes
    /theme worldcup                       # Switch to World Cup theme
"""

from __future__ import annotations

import argparse
import atexit
import codecs
from contextlib import contextmanager
from dataclasses import dataclass, field
import getpass
import json
import os
import select
import sys
import termios
import textwrap
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.cells import cell_len
from rich import box

from deepforge.config import ApprovalPolicy, Backend, Mode, config
from deepforge.session import Session, SessionConfig
from deepforge.agent import AgentResponse
import themes

console = Console()
_READLINE_READY = False


# ── Helpers ────────────────────────────────────────────────────────

def check_api_key(backend: str = "deepseek", yaml_data: dict | None = None) -> bool:
    yaml_data = yaml_data or {}
    if backend == "azure":
        az = yaml_data.get("azure", {}) or {}
        key = (
            az.get("api_key")
            or
            os.environ.get("AZURE_OPENAI_API_KEY")
            or os.environ.get("DEEPFORGE_AZURE_API_KEY")
            or os.environ.get("CODEX_AZURE_API_KEY")
        )
        key_name = "Azure OpenAI API key"
        env_cmd = "export AZURE_OPENAI_API_KEY='your-key-here'"
    else:
        ds = yaml_data.get("deepseek", {}) or {}
        key = (
            ds.get("api_key")
            or
            os.environ.get("DEEPFORGE_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("CODEX_API_KEY")
        )
        key_name = "DeepSeek API key"
        env_cmd = "export DEEPSEEK_API_KEY='sk-your-key-here'"

    if not key:
        console.print(
            Panel(
                f"[bold red]{key_name} not found![/]\n\n"
                "Set it with:\n"
                f"  [bold]{env_cmd}[/]\n\n"
                "Or configure via [bold]config/env.yaml[/]",
                title="❌ Configuration Error",
                border_style="red",
            )
        )
        return False
    return True


def configure_line_editor() -> None:
    """Enable editable input and persistent history when readline is available."""
    global _READLINE_READY
    if _READLINE_READY:
        return

    try:
        import readline
    except ImportError:
        _READLINE_READY = True
        return

    try:
        readline.parse_and_bind("set editing-mode emacs")

        # ── Arrow keys (VT100 / ANSI) ─────────────────────────
        readline.parse_and_bind('"\\e[D": backward-char')
        readline.parse_and_bind('"\\e[C": forward-char')
        readline.parse_and_bind('"\\e[A": previous-history')
        readline.parse_and_bind('"\\e[B": next-history')

        # ── Delete key ────────────────────────────────────────
        readline.parse_and_bind('"\\e[3~": delete-char')

        # ── Home / End keys ───────────────────────────────────
        readline.parse_and_bind('"\\e[H": beginning-of-line')
        readline.parse_and_bind('"\\e[F": end-of-line')
        readline.parse_and_bind('"\\e[1~": beginning-of-line')   # Linux console
        readline.parse_and_bind('"\\e[4~": end-of-line')         # Linux console

        # ── Option+← / Option+→  word navigation (macOS) ──────
        readline.parse_and_bind('"\\eb": backward-word')        # iTerm2
        readline.parse_and_bind('"\\ef": forward-word')         # iTerm2
        readline.parse_and_bind('"\\e[1;3D": backward-word')    # Terminal.app
        readline.parse_and_bind('"\\e[1;3C": forward-word')     # Terminal.app

        # ── Option+Delete  kill backward word (macOS) ─────────
        readline.parse_and_bind('"\\e\\C-h": backward-kill-word')
        readline.parse_and_bind('"\\e\\C-?": backward-kill-word')

        # ── Ctrl+← / Ctrl+→  word navigation ──────────────────
        readline.parse_and_bind('"\\e[1;5D": backward-word')
        readline.parse_and_bind('"\\e[1;5C": forward-word')
    except Exception:
        pass

    history_path = Path.home() / ".deepforge_history"
    try:
        history_path.touch(exist_ok=True)
        readline.read_history_file(str(history_path))
        readline.set_history_length(1000)
        atexit.register(readline.write_history_file, str(history_path))
    except Exception:
        pass

    _READLINE_READY = True


def read_user_input() -> str:
    """Read one editable prompt line from the terminal."""
    configure_line_editor()
    return console.input("[bold cyan]deepforge[/]› ").strip()


def _format_tool_arguments(arguments: dict) -> str:
    text = json.dumps(arguments or {}, ensure_ascii=False, indent=2)
    if len(text) > 1000:
        return text[:1000] + "\n... (truncated)"
    return text


def create_tui_approval_callback():
    """Create an interactive approval callback for suggest-mode tools."""

    def approve(tool, tool_call, gate_result) -> bool:
        console.print()
        console.print(Panel(
            f"[bold]Tool:[/] {tool_call.function_name}\n"
            f"[bold]Reason:[/] {gate_result.reason}\n\n"
            f"[bold]Arguments:[/]\n{_format_tool_arguments(tool_call.arguments)}",
            title="Approval Required",
            border_style="yellow",
        ))
        answer = console.input("Approve this tool call? [bold][y/N][/]: ").strip().lower()
        return answer in {"y", "yes"}

    return approve


def mode_color(mode: Mode) -> str:
    return {
        Mode.AGENT: "bold green",
        Mode.PLAN: "bold yellow",
        Mode.YOLO: "bold red",
    }.get(mode, "white")


def policy_color(policy: ApprovalPolicy) -> str:
    return {
        ApprovalPolicy.AUTO: "green",
        ApprovalPolicy.SUGGEST: "yellow",
        ApprovalPolicy.NEVER: "red",
    }.get(policy, "white")


def pressure_color(pressure: str) -> str:
    return {
        "low": "green",
        "medium": "yellow",
        "high": "orange1",
        "critical": "red",
    }.get(pressure, "white")


def get_term_width() -> int:
    """Get current terminal width, with fallback."""
    try:
        return console.width
    except Exception:
        import shutil
        return shutil.get_terminal_size().columns


def pressure_bar(pressure: str, ratio: float) -> Text:
    """Build a color-coded pressure bar that adapts to terminal width."""
    tw = get_term_width()
    # Scale bar width with terminal: 8 on narrow, up to 15 on wide
    if tw < 80:
        bar_width = 8
    elif tw < 120:
        bar_width = 10
    else:
        bar_width = 14

    filled = int(ratio * bar_width)
    empty = bar_width - filled
    color = pressure_color(pressure)
    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * empty, style="dim")
    bar.append(f" {ratio:.0%}", style="dim")
    return bar


# ── Response Rendering ─────────────────────────────────────────────

def render_response(response: AgentResponse) -> None:
    """Render the agent's response with metadata."""
    if response.content:
        console.print()
        console.print(response.content)
    else:
        console.print("[dim](no response)[/]")

    footer_parts = []
    if response.tool_results:
        display_names = [r.tool_name or r.tool_call_id[:8] for r in response.tool_results[:3]]
        names_str = ", ".join(display_names)
        footer_parts.append(f"{len(response.tool_results)} tool(s): {names_str}")
    footer_parts.append(f"{response.latency_ms:.0f}ms")
    footer_parts.append(f"{response.total_tokens_used:,} tokens")

    footer = " · ".join(footer_parts)
    console.print(f"[dim]── {footer} ──[/]")
    console.print()


def render_error(error: str) -> None:
    """Render an error message."""
    console.print(f"\n[bold red]❌ {error}[/]\n")


# ── Tool Progress Display ──────────────────────────────────────────

class ToolProgressDisplay:
    """Shows real-time tool execution progress."""

    def __init__(self):
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            expand=False,
        )
        self.active = False

    def __enter__(self):
        self.progress.__enter__()
        self.active = True
        return self

    def __exit__(self, *args):
        self.progress.__exit__(*args)
        self.active = False

    def add_tool(self, name: str, description: str = ""):
        if self.active:
            task_id = self.progress.add_task(f"[cyan]{name}[/] {description}", total=None)
            return task_id

    def complete_tool(self, task_id, success: bool = True):
        if self.active and task_id is not None:
            style = "green" if success else "red"
            symbol = "✓" if success else "✗"
            self.progress.update(task_id, description=f"[{style}]{symbol} Done[/]")


# ── CodeWhale-style Shell ────────────────────────────────────────────

# Default CodeWhale colors (used as fallback when no theme is active)
_DEFAULT_CW = {
    "bg": "default",
    "surface": "default",
    "surface_hi": "default",
    "border": "#24508A",
    "border_dim": "#17345F",
    "blue": "#58A6FF",
    "cyan": "#3DD6D0",
    "green": "#37E58F",
    "yellow": "#F4B74A",
    "orange": "#D98A35",
    "muted": "#7E8CA6",
    "text": "#D7E4F5",
    "dim_text": "#6F7D96",
    "error": "#FF6370",
    "icon": "🐳",
    "progress": "#58A6FF",
    "complete": "#58A6FF",
    "running": "#F4B74A",
    "failed": "#FF6370",
}


def _cw_value(key: str) -> str:
    """Get a CodeWhale theme token from the active theme, falling back to default."""
    theme = themes.get_active()
    if theme and theme.codewhale_colors:
        return theme.codewhale_colors.get(key, _DEFAULT_CW.get(key, "default"))
    return _DEFAULT_CW.get(key, "default")


def _cw_color(key: str) -> str:
    return _cw_value(key)


class _CWColor:
    """String-like dynamic theme color token."""

    def __init__(self, key: str):
        self.key = key

    def __str__(self) -> str:
        return _cw_color(self.key)

    def __format__(self, format_spec: str) -> str:
        return format(str(self), format_spec)


# Convenience color tokens (used throughout CodeWhaleShell)
BG = _CWColor("bg")
SURFACE = _CWColor("surface")
SURFACE_HI = _CWColor("surface_hi")
BORDER = _CWColor("border")
BORDER_DIM = _CWColor("border_dim")
BLUE = _CWColor("blue")
CYAN = _CWColor("cyan")
GREEN = _CWColor("green")
YELLOW = _CWColor("yellow")
ORANGE = _CWColor("orange")
MUTED = _CWColor("muted")
TEXT = _CWColor("text")
DIM_TEXT = _CWColor("dim_text")
ERROR = _CWColor("error")
PROGRESS = _CWColor("progress")
COMPLETE = _CWColor("complete")
RUNNING = _CWColor("running")
FAILED = _CWColor("failed")


@dataclass
class TranscriptBlock:
    kind: str
    content: str
    style: str = TEXT
    meta: str = ""


@dataclass
class TaskTurn:
    index: int
    status: str = "running"
    step: str = "thinking running"
    started_at: float = field(default_factory=time.time)
    finished_ms: float = 0.0
    events: list[tuple[str, str]] = field(default_factory=list)


@contextmanager
def raw_terminal_mode():
    """Temporarily switch stdin to cbreak mode so Live can refresh while editing."""
    if not sys.stdin.isatty():
        yield
        return

    fd = sys.stdin.fileno()
    try:
        old_attrs = termios.tcgetattr(fd)
    except termios.error:
        yield
        return

    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?1000h\x1b[?1006h")
        sys.stdout.flush()
        yield
    finally:
        sys.stdout.write("\x1b[?1006l\x1b[?1000l")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


class CodeWhaleShell:
    """Full-screen adaptive Rich UI modeled after the CodeWhale terminal shell."""

    REFRESH_SECONDS = 0.08

    def __init__(self, session: Session, *, theme_name: str = "default"):
        self.session = session
        self.theme_name = theme_name
        self.username = getpass.getuser()
        self.model = getattr(session.client, "model", None) or config.model
        self.version = self._version()

        self.transcript: list[TranscriptBlock] = []
        self.tasks: list[TaskTurn] = []
        self.command_history: list[str] = []
        self.history_index: int | None = None

        self.input_buffer = ""
        self.cursor = 0
        self.composer_label = "Composer"
        self.composer_hint = "编写任务或使用 /。"
        self.footer_state = "turn completed"
        self.scroll_offset = 0
        self._last_transcript_total_lines = 0
        self._last_transcript_visible_lines = 1
        self.running = True
        self.live: Live | None = None
        self._stdin_decoder = codecs.getincrementaldecoder("utf-8")()
        self._last_render_size = (console.width, console.height)

    @staticmethod
    def _version() -> str:
        try:
            from deepforge import __version__
            return __version__
        except Exception:
            return "0.1.0"

    def run(self) -> None:
        """Run the adaptive shell until the user exits."""
        self._add_system("DeepForge ready. Use /help for commands.")
        session_callback = self.session.agent.approval_callback if self.session.agent else None
        if self.session.agent:
            self.session.agent.approval_callback = self._approve_tool_call

        try:
            with raw_terminal_mode(), Live(
                self._layout(),
                console=console,
                screen=True,
                auto_refresh=False,
                transient=False,
            ) as live:
                self.live = live
                while self.running:
                    try:
                        user_input = self._read_line()
                    except KeyboardInterrupt:
                        self._add_system("Interrupted. Press Ctrl-D or type /exit to quit.")
                        self._refresh()
                        continue
                    except EOFError:
                        break

                    if not user_input:
                        continue

                    self.command_history.append(user_input)
                    self.history_index = None
                    self._add_user(user_input)
                    self._refresh()

                    if user_input.startswith("/"):
                        self.running = self._handle_live_command(user_input)
                        self._refresh()
                        continue

                    self._process_agent_turn(user_input)
        finally:
            if self.session.agent:
                self.session.agent.approval_callback = session_callback
            self.live = None

    # ── Layout Rendering ────────────────────────────────────────

    def _layout(self) -> Layout:
        width = console.width
        height = console.height
        composer_size = 3
        footer_size = 1 if height >= 10 else 0
        body_height = max(4, height - 1 - composer_size - footer_size)

        root = Layout(name="root")
        rows = [
            Layout(self._header(), name="header", size=1),
            Layout(name="body", ratio=1, minimum_size=4),
            Layout(self._composer(), name="composer", size=composer_size),
        ]
        if footer_size:
            rows.append(Layout(self._footer(), name="footer", size=footer_size))
        root.split_column(*rows)

        if width >= 112 and height >= 15:
            task_width = min(48, max(34, int(width * 0.29)))
            root["body"].split_row(
                Layout(self._transcript_panel(visible_lines=body_height), name="transcript", ratio=1),
                Layout(self._tasks_panel(), name="tasks", size=task_width),
            )
        elif height >= 18:
            task_height = min(8, max(5, height // 4))
            transcript_height = max(1, body_height - task_height)
            root["body"].split_column(
                Layout(self._transcript_panel(visible_lines=transcript_height), name="transcript", ratio=1),
                Layout(self._tasks_panel(compact=True), name="tasks", size=task_height),
            )
        else:
            root["body"].update(self._transcript_panel(visible_lines=body_height, compact=True))

        return root

    def _header(self) -> Table:
        stats = self.session.get_context_stats()
        ratio_str = stats.get("usage_ratio", "0%")
        try:
            ratio = float(str(ratio_str).rstrip("%")) / 100
        except ValueError:
            ratio = 0.0

        left = Text.assemble(
            ("Agent", f"bold {PROGRESS} on {BG}"),
            ("  ", f"on {BG}"),
            (self.username, f"bold {TEXT} on {BG}"),
            (" · ", f"dim on {BG}"),
            (self.model, f"{MUTED} on {BG}"),
        )
        right = Text.assemble(
            (_cw_value("icon"), f"{PROGRESS} on {BG}"),
            (" · ", f"dim on {BG}"),
            (self.session.policy.value, f"bold {YELLOW} on {BG}"),
            ("  ", f"on {BG}"),
            (f"{ratio:.0%}", f"bold {PROGRESS} on {BG}"),
            (" ", f"on {BG}"),
            self._mini_pressure_bar(ratio),
            ("  ", f"on {BG}"),
            (f"v{self.version}", f"{MUTED} on {BG}"),
        )

        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="right")
        grid.add_row(left, right)
        return grid

    def _mini_pressure_bar(self, ratio: float) -> Text:
        width = 4
        filled = min(width, max(0, int(round(ratio * width))))
        bar = Text(style=f"on {BG}")
        bar.append("▰" * filled, style=f"{PROGRESS} on {BG}")
        bar.append("▱" * (width - filled), style=f"{BORDER_DIM} on {BG}")
        return bar

    def _transcript_panel(self, *, visible_lines: int, compact: bool = False) -> Padding:
        available_width = max(24, console.width - (52 if console.width >= 112 else 4))
        content = self._render_transcript(available_width, visible_lines=visible_lines, compact=compact)
        return Padding(content, (0, 0), style=f"on {BG}")

    def _render_transcript(self, width: int, *, visible_lines: int, compact: bool = False) -> Group:
        visible_lines = max(1, visible_lines)
        renderables: list[Text] = []
        for block in self.transcript:
            renderables.extend(self._render_block(block, width))
        if not renderables:
            blank = Text("DeepForge", style=f"bold {BLUE} on {BG}")
            blank.append(" waits for a task.", style=f"{MUTED} on {BG}")
            renderables.append(blank)

        self._last_transcript_total_lines = len(renderables)
        self._last_transcript_visible_lines = visible_lines
        max_scroll = max(0, len(renderables) - visible_lines)
        self.scroll_offset = min(max(0, self.scroll_offset), max_scroll)

        if self.scroll_offset:
            end = max(visible_lines, len(renderables) - self.scroll_offset)
            start = max(0, end - visible_lines)
        else:
            start = max(0, len(renderables) - visible_lines)
            end = len(renderables)

        return Group(*renderables[start:end])

    def _render_block(self, block: TranscriptBlock, width: int) -> list[Text]:
        lines: list[Text] = []
        wrap_width = max(20, width - 4)
        chunks = self._wrap(block.content, wrap_width)

        if block.kind == "user":
            first = Text(style=f"on {BG}")
            first.append("▎ ", style=f"bold {GREEN} on {SURFACE_HI}")
            first.append(chunks[0] if chunks else "", style=f"bold {GREEN} on {SURFACE_HI}")
            lines.append(first)
            for extra in chunks[1:]:
                line = Text("  " + extra, style=f"{GREEN} on {SURFACE_HI}")
                lines.append(line)
            return lines

        if block.kind == "assistant":
            if block.meta:
                meta = Text("… ", style=f"{DIM_TEXT} on {BG}")
                meta.append(block.meta, style=f"bold {TEXT} on {BG}")
                lines.append(meta)
            if not chunks:
                chunks = [""]
            first = Text("● ", style=f"{BLUE} on {BG}")
            first.append(chunks[0], style=f"{TEXT} on {BG}")
            lines.append(first)
            for extra in chunks[1:]:
                lines.append(Text("  " + extra, style=f"{TEXT} on {BG}"))
            return lines

        if block.kind == "tool":
            for chunk in chunks:
                line = Text("→ ", style=f"{CYAN} on {BG}")
                line.append(chunk, style=f"{MUTED} on {BG}")
                lines.append(line)
            return lines

        if block.kind == "terminal":
            is_error = str(block.style) in {str(ERROR), str(FAILED)}
            accent = FAILED if is_error else PROGRESS
            marker = "✗" if is_error else "▸"
            header = Text(f"{marker} terminal", style=f"bold {accent} on {BG}")
            if block.meta:
                header.append(f" · {block.meta}", style=f"{MUTED} on {BG}")
            lines.append(header)
            for chunk in chunks:
                body = Text("│ ", style=f"{BORDER_DIM} on {BG}")
                body.append(chunk, style=f"{block.style} on {BG}")
                lines.append(body)
            return lines

        if block.kind == "error":
            for chunk in chunks:
                line = Text("✗ ", style=f"bold {ERROR} on {BG}")
                line.append(chunk, style=f"{ERROR} on {BG}")
                lines.append(line)
            return lines

        for chunk in chunks:
            lines.append(Text(chunk, style=f"{block.style} on {BG}"))
        return lines

    @staticmethod
    def _wrap(content: str, width: int) -> list[str]:
        if not content:
            return [""]
        lines: list[str] = []
        for raw_line in content.splitlines() or [""]:
            wrapped = textwrap.wrap(
                raw_line,
                width=width,
                replace_whitespace=False,
                drop_whitespace=False,
            )
            lines.extend(wrapped or [""])
        return lines

    def _tasks_panel(self, *, compact: bool = False) -> Panel:
        body = self._render_tasks(compact=compact)
        return Panel(
            body,
            title=Text(" Tasks ", style=f"bold {YELLOW}"),
            title_align="left",
            box=box.SQUARE,
            border_style=str(BORDER),
            padding=(0, 1),
            style=f"on {BG}",
        )

    def _render_tasks(self, *, compact: bool = False) -> Group:
        if not self.tasks:
            return Group(Text("No turns yet", style=f"{DIM_TEXT} on {BG}"))

        visible = self.tasks[-3:] if compact else self.tasks[-6:]
        lines: list[Text] = []
        for task in visible:
            status_style = self._status_style(task.status)
            title = Text(style=f"on {BG}")
            title.append(f"Turn {task.index}", style=f"bold {YELLOW} on {BG}")
            title.append(f" ({task.status})", style=f"{status_style} on {BG}")
            lines.append(title)

            elapsed = task.finished_ms / 1000 if task.finished_ms else time.time() - task.started_at
            step = Text(style=f"on {BG}")
            step_style = RUNNING if task.status == "running" else MUTED
            step.append(task.step, style=f"bold {step_style} on {BG}")
            if task.status == "running":
                step.append(f" {elapsed:.1f}s", style=f"{MUTED} on {BG}")
            lines.append(step)

            for name, state in task.events[-4:]:
                state_style = COMPLETE if state == "done" else FAILED if state == "fail" else PROGRESS
                event_line = Text("  ", style=f"on {BG}")
                event_line.append(state, style=f"{state_style} on {BG}")
                event_line.append(f" {name}", style=f"{MUTED} on {BG}")
                lines.append(event_line)
            if not compact:
                lines.append(Text("", style=f"on {BG}"))
        return Group(*lines)

    def _status_style(self, status: str) -> _CWColor:
        if status == "completed":
            return COMPLETE
        if status == "running":
            return RUNNING
        return FAILED

    def _composer(self) -> Panel:
        line = Text(style=f"on {SURFACE}")
        prompt = "▎ "
        line.append(prompt, style=f"bold {GREEN} on {SURFACE}")

        if self.input_buffer:
            max_input_cells = max(8, console.width - 8)
            before, at_cursor, after = self._visible_input_segments(max_input_cells)
            line.append(before, style=f"{TEXT} on {SURFACE}")
            line.append(at_cursor, style=f"{TEXT} on {SURFACE}")
            line.append(after, style=f"{TEXT} on {SURFACE}")
        else:
            line.append(self.composer_hint, style=f"{DIM_TEXT} on {SURFACE}")

        return Panel(
            line,
            title=Text(self.composer_label, style=f"{MUTED}"),
            title_align="left",
            subtitle=Text(f" {self.footer_state} ", style=f"{CYAN}"),
            subtitle_align="right",
            box=box.SQUARE,
            border_style=str(BORDER),
            padding=(0, 1),
            style=f"on {SURFACE}",
        )

    def _visible_input_segments(self, max_cells: int) -> tuple[str, str, str]:
        """Return a single-line view of the input around the cursor."""
        text = self.input_buffer
        cursor = min(max(0, self.cursor), len(text))
        if cell_len(text) <= max_cells:
            return text[:cursor], text[cursor : cursor + 1], text[cursor + 1 :]

        cursor_text = text[cursor : cursor + 1]
        cursor_cells = cell_len(cursor_text) if cursor_text else 1
        before_budget = max(0, max_cells - cursor_cells)

        start = cursor
        used_before = 0
        while start > 0:
            char_cells = cell_len(text[start - 1 : start])
            if used_before + char_cells > before_budget:
                break
            start -= 1
            used_before += char_cells

        end = cursor + (1 if cursor_text else 0)
        used_total = used_before + cursor_cells
        while end < len(text):
            char_cells = cell_len(text[end : end + 1])
            if used_total + char_cells > max_cells:
                break
            end += 1
            used_total += char_cells

        return text[start:cursor], cursor_text, text[cursor + 1 : end]

    def _composer_cursor_position(self) -> tuple[int, int]:
        height = console.height
        footer_size = 1 if height >= 10 else 0
        composer_size = 3
        body_height = max(4, height - 1 - composer_size - footer_size)

        # 1-based terminal coordinates. The composer input row is the middle row
        # of the 3-line panel; column 5 is after border, padding, and prompt.
        row = min(height, max(1, body_height + 3))
        if self.input_buffer:
            max_input_cells = max(8, console.width - 8)
            before, _, _ = self._visible_input_segments(max_input_cells)
            col = 5 + cell_len(before)
        else:
            col = 5
        return row, min(console.width, max(1, col))

    def _place_terminal_cursor(self) -> None:
        if not sys.stdin.isatty():
            return
        row, col = self._composer_cursor_position()
        output = self.live.console.file if self.live else console.file
        output.write(f"\x1b[{row};{col}H\x1b[?25h")
        output.flush()

    def _footer(self) -> Table:
        stats = self.session.stats()
        client = self.session.client
        hit = getattr(client, "cache_hit_tokens", 0) if client else 0
        miss = getattr(client, "cache_miss_tokens", 0) if client else 0
        tokens = stats.get("api_tokens_used", 0)
        footer_style = COMPLETE if self.footer_state == "turn completed" else RUNNING if self.footer_state == "turn running" else FAILED

        left = Text.assemble(
            ("✓ ", f"bold {footer_style} on {BG}"),
            (self.footer_state, f"bold {footer_style} on {BG}"),
        )
        if self.scroll_offset:
            left.append(f"  view -{self.scroll_offset} lines", style=f"{YELLOW} on {BG}")
        right = Text(
            f"Cache: hit {hit:,} | miss {miss:,}  tok {tokens/1000:.1f}k",
            style=f"{MUTED} on {BG}",
        )
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="right")
        grid.add_row(left, right)
        return grid

    # ── Input Handling ─────────────────────────────────────────

    def _read_line(self, *, prompt: str | None = None) -> str:
        original_hint = self.composer_hint
        if prompt is not None:
            self.composer_hint = prompt
        self.input_buffer = ""
        self.cursor = 0
        self._refresh()

        try:
            while True:
                key = self._read_key(timeout=self.REFRESH_SECONDS)
                if key is None:
                    self._refresh_if_resized()
                    continue
                result = self._apply_key(key)
                if result == "submit":
                    line = self.input_buffer.strip()
                    self.input_buffer = ""
                    self.cursor = 0
                    self.composer_hint = original_hint
                    self._refresh()
                    return line
                if result == "eof":
                    raise EOFError
                if result == "interrupt":
                    raise KeyboardInterrupt
                self._refresh()
        finally:
            self.composer_hint = original_hint

    def _read_key(self, *, timeout: float) -> str | None:
        if not sys.stdin.isatty():
            line = sys.stdin.readline()
            if line == "":
                raise EOFError
            return line

        fd = sys.stdin.fileno()
        readable, _, _ = select.select([fd], [], [], timeout)
        if not readable:
            return None

        data = os.read(fd, 32)
        if not data:
            raise EOFError
        key = self._stdin_decoder.decode(data)
        if not key:
            return None
        if key == "\x1b":
            while True:
                more, _, _ = select.select([fd], [], [], 0.001)
                if not more:
                    break
                more_data = os.read(fd, 8)
                if not more_data:
                    break
                key += self._stdin_decoder.decode(more_data)
                if key.endswith("~") or key.endswith(("A", "B", "C", "D", "F", "H")):
                    break
        return key

    def _apply_key(self, key: str) -> str | None:
        if "\n" in key or "\r" in key:
            if len(key) > 1 and not key.startswith("\x1b"):
                self.input_buffer = key.strip()
            return "submit"
        if key == "\x03":
            return "interrupt"
        if key == "\x04":
            return "eof" if not self.input_buffer else None
        if key in ("\x7f", "\b"):
            if self.cursor > 0:
                self.input_buffer = self.input_buffer[: self.cursor - 1] + self.input_buffer[self.cursor :]
                self.cursor -= 1
            return None
        if key == "\x1b[D":
            self.cursor = max(0, self.cursor - 1)
            return None
        if key == "\x1b[C":
            self.cursor = min(len(self.input_buffer), self.cursor + 1)
            return None
        if key in ("\x1b[H", "\x1b[1~"):
            self.cursor = 0
            return None
        if key in ("\x1b[F", "\x1b[4~"):
            self.cursor = len(self.input_buffer)
            return None
        if key == "\x1b[5~":
            self._scroll_transcript(self._scroll_page_size())
            return None
        if key == "\x1b[6~":
            self._scroll_transcript(-self._scroll_page_size())
            return None
        if key == "\x1b[1;2A":
            self._scroll_transcript(1)
            return None
        if key == "\x1b[1;2B":
            self._scroll_transcript(-1)
            return None
        if key.startswith("\x1b[<") and key.endswith("M"):
            self._apply_mouse_event(key)
            return None
        if key == "\x1b[3~":
            if self.cursor < len(self.input_buffer):
                self.input_buffer = self.input_buffer[: self.cursor] + self.input_buffer[self.cursor + 1 :]
            return None
        if key == "\x1b[A":
            self._history_previous()
            return None
        if key == "\x1b[B":
            self._history_next()
            return None
        if key.startswith("\x1b"):
            return None

        for ch in key:
            if ch.isprintable():
                self.input_buffer = self.input_buffer[: self.cursor] + ch + self.input_buffer[self.cursor :]
                self.cursor += 1
        return None

    def _scroll_page_size(self) -> int:
        return max(4, self._last_transcript_visible_lines - 1)

    def _scroll_transcript(self, delta: int) -> None:
        max_scroll = max(0, self._last_transcript_total_lines - self._last_transcript_visible_lines)
        self.scroll_offset = min(max(0, self.scroll_offset + delta), max_scroll)

    def _apply_mouse_event(self, key: str) -> None:
        try:
            button = int(key[3:].split(";", 1)[0])
        except (ValueError, IndexError):
            return
        if button == 64:
            self._scroll_transcript(3)
        elif button == 65:
            self._scroll_transcript(-3)

    def _history_previous(self) -> None:
        if not self.command_history:
            return
        if self.history_index is None:
            self.history_index = len(self.command_history) - 1
        else:
            self.history_index = max(0, self.history_index - 1)
        self.input_buffer = self.command_history[self.history_index]
        self.cursor = len(self.input_buffer)

    def _history_next(self) -> None:
        if self.history_index is None:
            return
        self.history_index += 1
        if self.history_index >= len(self.command_history):
            self.history_index = None
            self.input_buffer = ""
        else:
            self.input_buffer = self.command_history[self.history_index]
        self.cursor = len(self.input_buffer)

    # ── Agent + Commands ───────────────────────────────────────

    def _process_agent_turn(self, user_input: str) -> None:
        task = TaskTurn(index=len(self.tasks) + 1)
        self.tasks.append(task)
        assistant = TranscriptBlock("assistant", "", meta="reasoning running")
        self.transcript.append(assistant)
        self.scroll_offset = 0
        self.footer_state = "turn running"
        tool_count = 0
        self._refresh()

        try:
            for event in self.session.agent.process_stream(user_input):
                etype = event["type"]
                if etype == "text":
                    assistant.content += event["content"]
                    task.step = "model reasoning"
                elif etype == "tool_start":
                    tool_count += 1
                    name = event.get("name", "tool")
                    task.step = "tool running"
                    task.events.append((name, "run"))
                    args = event.get("args") or {}
                    self.transcript.append(TranscriptBlock(
                        "terminal",
                        self._tool_notice_command(name, args),
                        style=PROGRESS,
                        meta="tool call",
                    ))
                    self.scroll_offset = 0
                elif etype == "tool_end":
                    name = event.get("name", "tool")
                    success = bool(event.get("success"))
                    task.events.append((name, "done" if success else "fail"))
                    output = event.get("output") or ""
                    if success:
                        self.transcript.append(TranscriptBlock(
                            "terminal",
                            f"{name} completed",
                            style=COMPLETE,
                            meta="tool done",
                        ))
                    else:
                        detail = f"{name} failed"
                        if output:
                            detail += f"\n{self._short_text(output, 360)}"
                        self.transcript.append(TranscriptBlock("terminal", detail, style=FAILED, meta="tool failed"))
                    self.scroll_offset = 0
                elif etype == "done":
                    task.status = "completed"
                    task.finished_ms = float(event.get("ms", 0))
                    task.step = "reasoning done"
                    if not assistant.content and event.get("content"):
                        assistant.content = event["content"]
                    latency = task.finished_ms / 1000
                    tokens = int(event.get("tokens", 0) or 0)
                    parts = [f"reasoning done · {latency:.1f}s"]
                    if tool_count:
                        parts.append(f"{tool_count} tool(s)")
                    if tokens:
                        parts.append(f"{tokens:,} tok")
                    assistant.meta = "  ".join(parts)
                    self.footer_state = "turn completed"
                elif etype == "error":
                    task.status = "failed"
                    task.step = "error"
                    self.footer_state = "turn failed"
                    self.transcript.append(TranscriptBlock("error", event["error"]))
                    self.scroll_offset = 0
                self._refresh()
        except KeyboardInterrupt:
            task.status = "failed"
            task.step = "interrupted"
            self.footer_state = "turn interrupted"
            self.transcript.append(TranscriptBlock("terminal", "request interrupted", style=FAILED, meta="interrupt"))
            self.scroll_offset = 0
            self._refresh()
        except Exception as exc:
            task.status = "failed"
            task.step = "error"
            self.footer_state = "turn failed"
            self.transcript.append(TranscriptBlock("error", str(exc)))
            self.scroll_offset = 0
            self._refresh()

        if self.session.context and self.session.context.needs_compaction:
            ctx = self.session.get_context_stats()
            self._add_system(f"Context {ctx.get('usage_ratio', '?')} — use /compact.")
        self._refresh()

    def _approve_tool_call(self, tool, tool_call, gate_result) -> bool:
        args = _format_tool_arguments(tool_call.arguments)
        self._add_system(
            f"Approval required for {tool_call.function_name}\n"
            f"Reason: {gate_result.reason}\n"
            f"Arguments:\n{args}"
        )
        self._refresh()
        try:
            answer = self._read_line(prompt="Approve this tool call? [y/N]")
        except (KeyboardInterrupt, EOFError):
            return False
        return answer.strip().lower() in {"y", "yes"}

    def _handle_live_command(self, cmd: str) -> bool:
        parts = cmd.strip().split()
        command = parts[0].lower()

        if command in ("/exit", "/quit", "/q"):
            self._add_system("Goodbye.")
            return False

        if command == "/help":
            self._add_system(
                "Commands: /mode agent|plan|yolo, /policy auto|suggest|never, "
                "/tools, /mcp status|tools|reload, /stats, /context, /compact, "
                "/theme list|default|worldcup|dragonball, /clear, /exit"
            )
        elif command == "/mode":
            if len(parts) < 2:
                self._add_system(f"Current mode: {self.session.mode.value}")
            else:
                try:
                    new_mode = Mode(parts[1].lower())
                    self.session.set_mode(new_mode)
                    self._add_system(f"Mode changed to {new_mode.value}.")
                except ValueError:
                    self._add_error(f"Invalid mode: {parts[1]}")
        elif command == "/policy":
            if len(parts) < 2:
                self._add_system(f"Current policy: {self.session.policy.value}")
            else:
                try:
                    new_policy = ApprovalPolicy(parts[1].lower())
                    self.session.set_approval_policy(new_policy)
                    self._add_system(f"Policy changed to {new_policy.value}.")
                except ValueError:
                    self._add_error(f"Invalid policy: {parts[1]}")
        elif command == "/tools":
            tools = self.session.available_tools
            if not tools:
                self._add_system("No tools registered.")
            else:
                self._add_system("Available tools:\n" + "\n".join(f"- {name}" for name in tools))
        elif command == "/stats":
            stats = self.session.stats()
            lines = []
            for key, value in stats.items():
                if isinstance(value, dict):
                    value = ", ".join(f"{k}={v}" for k, v in value.items())
                lines.append(f"{key}: {value}")
            self._add_system("Session statistics:\n" + "\n".join(lines))
        elif command == "/context":
            ctx = self.session.get_context_stats()
            if ctx:
                self._add_system("Context:\n" + "\n".join(f"{k}: {v}" for k, v in ctx.items()))
            else:
                self._add_system("Context not initialized.")
        elif command == "/compact":
            result = self.session.compact()
            if result.get("compacted"):
                self._add_system(
                    f"Compacted {result.get('turns_compacted', 0)} turns, "
                    f"freed {result.get('tokens_freed', 0):,} tokens."
                )
            else:
                self._add_system(str(result.get("reason", "Compaction not needed.")))
        elif command == "/mcp":
            self._handle_mcp_command(parts)
        elif command == "/theme":
            self._handle_theme_command(parts)
        elif command == "/clear":
            self.transcript.clear()
            self.tasks.clear()
            self._add_system("Cleared.")
        else:
            self._add_error(f"Unknown command: {command}. Type /help for available commands.")
        return True

    def _handle_mcp_command(self, parts: list[str]) -> None:
        subcommand = parts[1].lower() if len(parts) > 1 else "status"
        if subcommand == "reload":
            self.session.reload_mcp()
            subcommand = "status"

        if subcommand == "status":
            status = self.session.mcp_status()
            lines = [f"enabled: {status.get('enabled')}"]
            if status.get("config_path"):
                lines.append(f"config: {status['config_path']}")
            if status.get("error"):
                lines.append(f"error: {status['error']}")
            servers = status.get("servers") or []
            if not servers:
                lines.append("servers: none")
            for server in servers:
                state = "connected" if server.get("connected") else f"error: {server.get('error') or 'not connected'}"
                lines.append(
                    f"- {server.get('name')} [{server.get('transport')}] {state} "
                    f"tools={server.get('tool_count', 0)}"
                )
            self._add_system("MCP status:\n" + "\n".join(lines))
        elif subcommand == "tools":
            tools = [name for name in self.session.available_tools if name.startswith("mcp__")]
            self._add_system("MCP tools:\n" + ("\n".join(f"- {name}" for name in tools) if tools else "none"))
        else:
            self._add_error("Usage: /mcp status|tools|reload")

    def _handle_theme_command(self, parts: list[str]) -> None:
        if len(parts) < 2 or parts[1].lower() == "list":
            current = themes.get_active()
            lines = []
            for name, theme in sorted(themes.list_themes().items()):
                marker = " active" if current and current.name == name else ""
                lines.append(f"- {name}: {theme.label}{marker}")
            self._add_system("Available themes:\n" + "\n".join(lines))
            return

        name = parts[1].lower()
        if name in {"off", "reset"}:
            name = "default"
        try:
            themes.activate(name)
            self.theme_name = name
            self._add_system(f"Theme set to {name}. CodeWhale layout remains active.")
        except ValueError as exc:
            self._add_error(str(exc))

    # ── State helpers ──────────────────────────────────────────

    def _add_user(self, content: str) -> None:
        self.transcript.append(TranscriptBlock("user", content))
        self.scroll_offset = 0

    def _add_system(self, content: str) -> None:
        self.transcript.append(TranscriptBlock("system", content, style=MUTED))
        self.scroll_offset = 0

    def _add_error(self, content: str) -> None:
        self.transcript.append(TranscriptBlock("error", content))
        self.scroll_offset = 0

    @staticmethod
    def _short_text(text: str, limit: int) -> str:
        text = text.replace("\r", "")
        return text if len(text) <= limit else text[:limit].rstrip() + "\n... truncated"

    def _tool_notice_command(self, name: str, args: dict) -> str:
        if not args:
            return f"$ {name}"
        text = json.dumps(args, ensure_ascii=False, separators=(",", ": "))
        return f"$ {name} {self._short_text(text, 300)}"

    def _refresh(self) -> None:
        if self.live:
            self._last_render_size = (console.width, console.height)
            self.live.update(self._layout(), refresh=True)
            self._place_terminal_cursor()

    def _refresh_if_resized(self) -> None:
        current_size = (console.width, console.height)
        if current_size != self._last_render_size:
            self._refresh()


# ── Interactive Loop ───────────────────────────────────────────────

def run_tui(session: Session, *, theme_name: str = "default") -> None:
    """Main TUI loop with a CodeWhale-style adaptive layout."""
    # Activate the selected theme
    try:
        themes.activate(theme_name)
    except ValueError:
        console.print(f"[yellow]⚠ Theme '{theme_name}' not found, using default[/]")
        themes.activate("default")
        theme_name = "default"

    CodeWhaleShell(session, theme_name=theme_name).run()


# ── Commands ────────────────────────────────────────────────────────

def handle_command(cmd: str, session: Session) -> bool:
    """Handle slash commands. Returns False to exit."""
    parts = cmd.strip().split()
    command = parts[0].lower()

    if command in ("/exit", "/quit", "/q"):
        console.print("[dim]Goodbye! 👋[/]")
        return False

    elif command == "/help":
        active = themes.get_active() or themes.get("default")
        console.print(active.render_banner(session))

    elif command == "/mode":
        if len(parts) < 2:
            console.print(f"Current mode: [{mode_color(session.mode)}]{session.mode.value}[/]")
            console.print("Usage: [bold]/mode agent|plan|yolo[/]")
        else:
            try:
                new_mode = Mode(parts[1].lower())
                session.set_mode(new_mode)
                console.print(f"[green]✓[/] Mode changed to [{mode_color(new_mode)}]{new_mode.value}[/]")
            except ValueError:
                console.print(f"[red]✗[/] Invalid mode: {parts[1]}")

    elif command == "/policy":
        if len(parts) < 2:
            console.print(f"Current policy: [{policy_color(session.policy)}]{session.policy.value}[/]")
            console.print("Usage: [bold]/policy auto|suggest|never[/]")
        else:
            try:
                new_policy = ApprovalPolicy(parts[1].lower())
                session.set_approval_policy(new_policy)
                console.print(f"[green]✓[/] Policy changed to [{policy_color(new_policy)}]{new_policy.value}[/]")
            except ValueError:
                console.print(f"[red]✗[/] Invalid policy: {parts[1]}")

    elif command == "/tools":
        tools = session.available_tools
        if tools:
            table = Table(title=f"Available Tools ({len(tools)})", box=box.SIMPLE)
            table.add_column("Tool", style="cyan")
            table.add_column("Type", style="dim")
            for t in tools:
                tool_obj = session.registry.get(t)
                if tool_obj is None:
                    tool_type = "❓ unknown"
                elif tool_obj.is_shell:
                    tool_type = "⚙️ shell"
                elif tool_obj.is_write:
                    tool_type = "✏️ write"
                elif tool_obj.requires_approval:
                    tool_type = "⚠ approval"
                elif tool_obj.is_network:
                    tool_type = "🌐 network"
                elif tool_obj.is_read:
                    tool_type = "📖 read"
                else:
                    tool_type = "❓ custom"
                table.add_row(t, tool_type)
            console.print(table)
        else:
            console.print("[dim]No tools registered.[/]")

    elif command == "/stats":
        stats = session.stats()
        table = Table(title="Session Statistics", box=box.SIMPLE)
        table.add_column("Metric", style="cyan")
        table.add_column("Value")
        for key, value in stats.items():
            if isinstance(value, dict):
                value = ", ".join(f"{k}={v}" for k, v in value.items())
            table.add_row(key.replace("_", " ").title(), str(value))
        console.print(table)

    elif command == "/mcp":
        _cmd_mcp(parts, session)

    elif command == "/context":
        ctx_stats = session.get_context_stats()
        if ctx_stats:
            pressure = ctx_stats.get("pressure", "low")
            context_line = Text("Context: ")
            context_line.append_text(pressure_bar(pressure, float(ctx_stats.get('usage_ratio', '0%').rstrip('%')) / 100))
            console.print(context_line)
            console.print(f"  Tokens: {ctx_stats.get('used_tokens', 0):,} / {ctx_stats.get('max_tokens', 0):,}")
            console.print(f"  Turns: {ctx_stats.get('active_turns', 0)}")
        else:
            console.print("[dim]Context not initialized.[/]")

    elif command == "/compact":
        result = session.compact()
        if result.get("compacted"):
            freed = result.get("tokens_freed", 0)
            turns = result.get("turns_compacted", 0)
            console.print(f"[green]✓[/] Compacted {turns} turns, freed {freed:,} tokens")
        else:
            console.print(f"[dim]ℹ[/] {result.get('reason', 'Not needed')}")

    elif command == "/theme":
        _cmd_theme(parts, session)

    elif command == "/clear":
        console.clear()
        active = themes.get_active() or themes.get("default")
        console.print(active.render_banner(session))

    else:
        console.print(f"[dim]Unknown command: {command}. Type /help for available commands.[/]")

    return True


def _cmd_mcp(parts: list[str], session: Session) -> None:
    """Handle the /mcp command."""
    subcommand = parts[1].lower() if len(parts) > 1 else "status"
    if subcommand == "reload":
        session.reload_mcp()
        console.print("[green]✓[/] MCP reloaded")
        subcommand = "status"

    if subcommand == "status":
        status = session.mcp_status()
        table = Table(title="MCP Status", box=box.SIMPLE)
        table.add_column("Server", style="cyan")
        table.add_column("Transport")
        table.add_column("State")
        table.add_column("Tools")
        table.add_column("Resources")
        table.add_column("Prompts")
        servers = status.get("servers") or []
        if not servers:
            table.add_row("-", "-", "disabled" if not status.get("enabled") else "none", "0", "0", "0")
        for server in servers:
            state = "connected" if server.get("connected") else f"error: {server.get('error') or 'not connected'}"
            table.add_row(
                str(server.get("name")),
                str(server.get("transport")),
                state,
                str(server.get("tool_count", 0)),
                str(server.get("resource_count", 0)),
                str(server.get("prompt_count", 0)),
            )
        console.print(table)
        if status.get("config_path"):
            console.print(f"[dim]Config: {status['config_path']}[/]")
        if status.get("error"):
            console.print(f"[red]{status['error']}[/]")
    elif subcommand == "tools":
        tools = [name for name in session.available_tools if name.startswith("mcp__")]
        if not tools:
            console.print("[dim]No MCP tools registered.[/]")
            return
        table = Table(title=f"MCP Tools ({len(tools)})", box=box.SIMPLE)
        table.add_column("Tool", style="cyan")
        for name in tools:
            table.add_row(name)
        console.print(table)
    else:
        console.print("Usage: [bold]/mcp status|tools|reload[/]")


def _cmd_theme(parts: list[str], session: Session) -> None:
    """Handle the /theme command."""
    if len(parts) < 2:
        # Show current theme and available themes
        current = themes.get_active()
        name = current.name if current else "default"
        console.print(f"Current theme: [bold green]{name}[/]")
        console.print("\nAvailable themes:")
        for t_name, t in sorted(themes.list_themes().items()):
            marker = " ◀ active" if current and t_name == current.name else ""
            console.print(f"  [bold cyan]{t_name}[/] — {t.label}{marker}")
        console.print("\nUsage: [bold]/theme <name>[/]  or  [bold]/theme off[/]  to reset to default")
        return

    sub = parts[1].lower()
    if sub in ("off", "default", "reset"):
        themes.activate("default")
        console.clear()
        console.print(themes.get_active().render_banner(session))
        console.print("[green]✓[/] Theme reset to [bold cyan]default[/].")
        return

    if sub == "list":
        console.print("Available themes:")
        for t_name, t in sorted(themes.list_themes().items()):
            console.print(f"  [bold cyan]{t_name}[/] — {t.label}")
        return

    # Activate a specific theme
    try:
        new_theme = themes.activate(sub)
        console.clear()
        console.print(new_theme.render_banner(session))

        # If the theme has a dashboard, render it
        if new_theme.render_dashboard:
            try:
                for renderable in new_theme.render_dashboard(session):
                    console.print()
                    console.print(renderable)
            except Exception:
                pass  # Dashboard is optional, failure shouldn't crash

        console.print()
        console.print(f"[green]✓[/] Theme switched to [bold cyan]{sub}[/].")
        console.print("[dim]Tip: /theme off to return to default.[/]")
    except ValueError as e:
        available = ", ".join(themes.list_themes().keys())
        console.print(f"[red]✗[/] {e}")
        console.print(f"[dim]Available: {available}[/]")


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DeepForge TUI — Rich Terminal Interface")
    parser.add_argument("--mode", choices=["agent", "plan", "yolo"], default=None)
    parser.add_argument("--policy", choices=["auto", "suggest", "never"], default=None)
    parser.add_argument("--workspace", "-w", default=None)
    parser.add_argument("--model", default=None,
                        help="Model to use (default: deepseek-chat for DeepSeek, gpt5.4 for Azure)")
    parser.add_argument("--backend", "-b", choices=["deepseek", "azure"], default=None,
                        help="Model backend: deepseek or azure (default: deepseek)")
    parser.add_argument("--config", default=None,
                        help="Path to env.yaml config file (default: auto-discover config/env.yaml)")
    parser.add_argument("--mcp-config", default=None,
                        help="MCP config path (default: ~/.deepforge/mcp.yaml)")
    parser.add_argument("--no-mcp", action="store_true",
                        help="Disable MCP integration for this session")
    parser.add_argument("--version", "-v", action="store_true")
    parser.add_argument("--theme", default="default",
                        help="Visual theme (default, worldcup, dragonball)")
    args = parser.parse_args()

    if args.version:
        from deepforge import __version__
        console.print(f"DeepForge v{__version__}")
        return

    # Determine backend (CLI arg > config file > env var > default)
    resolved_backend = args.backend
    config_path = Path(args.config).resolve() if args.config else None
    from deepforge.config import _discover_config_path, _load_yaml_config
    yaml_data = _load_yaml_config(config_path or _discover_config_path())
    if resolved_backend is None:
        resolved_backend = yaml_data.get("backend", "deepseek")
    resolved_backend = resolved_backend.lower()

    if not check_api_key(resolved_backend, yaml_data):
        sys.exit(1)

    session_config = SessionConfig(
        mode=Mode(args.mode) if args.mode else None,
        approval_policy=ApprovalPolicy(args.policy) if args.policy else None,
        workspace=Path(args.workspace).resolve() if args.workspace else None,
        model=args.model,
        backend=args.backend,
        config_path=Path(args.config).resolve() if args.config else None,
        mcp_enabled=not args.no_mcp,
        mcp_config_path=Path(args.mcp_config).expanduser() if args.mcp_config else None,
        approval_callback=create_tui_approval_callback(),
    )

    # Load YAML config into global config before session initialization
    if session_config.config_path or not args.backend:
        config_path = session_config.config_path or None
        from deepforge.config import Config
        yaml_config = Config.from_yaml(config_path)
        if args.backend:
            yaml_config.backend = Backend(args.backend)
        if args.mode:
            yaml_config.mode = Mode(args.mode)
        if args.policy:
            yaml_config.approval_policy = ApprovalPolicy(args.policy)
        if args.workspace:
            yaml_config.workspace = Path(args.workspace).resolve()
        if args.model:
            yaml_config.model = args.model
        config.__dict__.update(yaml_config.__dict__)

    session = Session(session_config=session_config)
    try:
        session.initialize()
        run_tui(session, theme_name=args.theme)
    finally:
        session.close()


if __name__ == "__main__":
    main()
