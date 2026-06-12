#!/usr/bin/env python3
"""
CodeX TUI — Rich terminal user interface.

Features:
- Color-coded panels and status bars
- Live streaming response display
- Tool execution progress indicators
- Context pressure gauge
- Command history with readline
- Mode/policy/workspace status bar

Usage:
    python tui.py                  # Interactive TUI
    python tui.py --mode yolo      # YOLO mode
    python tui.py --mode plan       # Read-only mode
"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.table import Table
from rich.columns import Columns
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from rich.status import Status
from rich.align import Align
from rich import box

from codex.config import ApprovalPolicy, Mode
from codex.session import Session, SessionConfig
from codex.agent import AgentResponse

# ── Color Theme ─────────────────────────────────────────────────────

THEME = {
    "banner": "bold cyan",
    "mode_agent": "bold green",
    "mode_plan": "bold yellow",
    "mode_yolo": "bold red",
    "policy_auto": "green",
    "policy_suggest": "yellow",
    "policy_never": "red",
    "tool": "dim cyan",
    "response": "white",
    "error": "bold red",
    "success": "green",
    "context_low": "green",
    "context_medium": "yellow",
    "context_high": "orange1",
    "context_critical": "red",
    "timestamp": "dim",
    "separator": "dim",
    "prompt": "bold white",
}

console = Console()
_READLINE_READY = False


# ── Helpers ────────────────────────────────────────────────────────

def check_api_key() -> bool:
    if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CODEX_API_KEY")):
        console.print(
            Panel(
                "[bold red]DeepSeek API key not found![/]\n\n"
                "Set it with:\n"
                "  [bold]export DEEPSEEK_API_KEY='sk-your-key-here'[/]",
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
        readline.parse_and_bind("\\e[D: backward-char")
        readline.parse_and_bind("\\e[C: forward-char")
        readline.parse_and_bind("\\e[A: previous-history")
        readline.parse_and_bind("\\e[B: next-history")
    except Exception:
        pass

    history_path = Path.home() / ".codex_history"
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
    return console.input("[bold cyan]codex[/]› ").strip()


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


def pressure_bar(pressure: str, ratio: float) -> Text:
    """Build a color-coded pressure bar like ████░░░░ 45%."""
    bar_width = 10
    filled = int(ratio * bar_width)
    empty = bar_width - filled
    color = pressure_color(pressure)
    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * empty, style="dim")
    bar.append(f" {ratio:.0%}", style="dim")
    return bar


# ── Banner ──────────────────────────────────────────────────────────

def render_banner(session: Session) -> Panel:
    """Render the welcome banner."""
    mode_str = session.mode.value.upper()
    policy_str = session.policy.value.upper()
    workspace_str = str(session.workspace)
    if len(workspace_str) > 50:
        workspace_str = "..." + workspace_str[-47:]

    text = Text()
    text.append("┌──────────────────────────────────────────────┐\n", style="dim cyan")
    text.append("│          ", style="dim cyan")
    text.append("CodeX v0.1.0", style="bold cyan")
    text.append("                                │\n", style="dim cyan")
    text.append("│     ", style="dim cyan")
    text.append("CodeWhale Architecture in Python", style="cyan")
    text.append("           │\n", style="dim cyan")
    text.append("├──────────────────────────────────────────────┤\n", style="dim cyan")
    text.append("│  ", style="dim cyan")
    text.append(f"Mode: [bold]{mode_str:<6}[/] ", style=mode_color(session.mode))
    text.append(f"Policy: [bold]{policy_str:<6}[/] ", style=policy_color(session.policy))
    text.append("    │\n", style="dim cyan")
    text.append("│  ", style="dim cyan")
    text.append(f"Tools: {len(session.available_tools):<2}  ")
    text.append(f"Workspace: {workspace_str:<34} │\n", style="dim")
    text.append("├──────────────────────────────────────────────┤\n", style="dim cyan")
    text.append("│  ", style="dim cyan")
    text.append("/mode /policy /tools /stats /compact /help /exit", style="dim")
    text.append("  │\n", style="dim cyan")
    text.append("└──────────────────────────────────────────────┘", style="dim cyan")

    return Panel(text, border_style="cyan")


def render_status_bar(session: Session) -> Text:
    """Render a compact status line."""
    ctx_stats = session.get_context_stats()
    pressure = ctx_stats.get("pressure", "low")
    ratio = float(ctx_stats.get("usage_ratio", "0%").rstrip("%")) / 100 if ctx_stats.get("usage_ratio") else 0.0

    bar = Text()
    bar.append(f" {session.mode.value.upper()} ", style=mode_color(session.mode))
    bar.append(f" {session.policy.value.upper()} ", style=policy_color(session.policy))
    bar.append("  Context: ", style="dim")
    bar.append_text(pressure_bar(pressure, ratio))
    bar.append(" ")
    bar.append(f"  Tools: {len(session.available_tools)}", style="dim")
    return bar


# ── Response Rendering ─────────────────────────────────────────────

def render_response(response: AgentResponse) -> None:
    """Render the agent's response with metadata."""
    # Print content
    if response.content:
        console.print()
        console.print(response.content)
    else:
        console.print("[dim](no response)[/]")

    # Print metadata footer
    footer_parts = []
    if response.tool_results:
        tool_names = [r.tool_call_id for r in response.tool_results[:3]]
        footer_parts.append(f"{len(response.tool_results)} tool(s)")
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


# ── Interactive Loop ───────────────────────────────────────────────

def run_tui(session: Session) -> None:
    """Main TUI loop with streaming output."""
    console.clear()
    console.print(render_banner(session))

    while True:
        try:
            console.print(render_status_bar(session))
            console.print()
            user_input = read_user_input()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye! 👋[/]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not handle_command(user_input, session):
                break
            continue

        # ── Streaming response (CodeWhale CLI style) ─────────
        console.print()  # blank line before response
        tool_count = 0
        all_tool_names: list[str] = []
        text_buffer = ""

        def flush_text_buffer(force: bool = False) -> None:
            nonlocal text_buffer
            wrap_at = max(40, console.width - 6)

            while "\n" in text_buffer:
                line, text_buffer = text_buffer.split("\n", 1)
                console.print(line, style="white")

            if force and text_buffer:
                console.print(text_buffer, style="white")
                text_buffer = ""
            elif len(text_buffer) >= wrap_at:
                console.print(text_buffer, style="white")
                text_buffer = ""

        try:
            for event in session.agent.process_stream(user_input):
                etype = event["type"]

                if etype == "text":
                    text_buffer += event["content"]
                    flush_text_buffer()

                elif etype == "tool_start":
                    flush_text_buffer(force=True)
                    tool_count += 1
                    tool_name = event["name"]
                    all_tool_names.append(tool_name)
                    console.print(f"[dim cyan]→ {tool_name}[/]", end="")
                    args = event.get("args") or {}
                    if args:
                        console.print(f" [dim]{args}[/]")
                    else:
                        console.print()

                elif etype == "tool_end":
                    flush_text_buffer(force=True)
                    tool_name = event.get("name", "tool")
                    success = event.get("success", False)
                    style = "green" if success else "red"
                    symbol = "✓" if success else "✗"
                    console.print(f"[{style}]{symbol} {tool_name}[/]")

                elif etype == "done":
                    flush_text_buffer(force=True)
                    total_tokens = event.get("tokens", 0)
                    latency = event.get("ms", 0)
                    console.print()
                    parts = []
                    if tool_count:
                        parts.append(f"{tool_count} tool(s)")
                    parts.append(f"{latency:.0f}ms")
                    parts.append(f"{total_tokens:,} tokens")
                    console.print(f"[dim]── {' · '.join(parts)} ──[/]")

                elif etype == "error":
                    flush_text_buffer(force=True)
                    console.print(f"\n[bold red]❌ {event['error']}[/]")

        except Exception as e:
            flush_text_buffer(force=True)
            console.print(f"\n[bold red]❌ {e}[/]")

        console.print()

        if session.context and session.context.needs_compaction:
            ctx = session.get_context_stats()
            console.print(f"[yellow]⚠ Context {ctx.get('usage_ratio', '?')} — /compact[/]")


# ── Commands ────────────────────────────────────────────────────────

def handle_command(cmd: str, session: Session) -> bool:
    """Handle slash commands. Returns False to exit."""
    parts = cmd.strip().split()
    command = parts[0].lower()

    if command in ("/exit", "/quit", "/q"):
        console.print("[dim]Goodbye! 👋[/]")
        return False

    elif command == "/help":
        console.print(render_banner(session))

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
                tool_type = "📖 read" if (tool_obj and tool_obj.is_read and not tool_obj.is_write) else "✏️ write" if (tool_obj and tool_obj.is_write) else "⚙️ shell"
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

    elif command == "/clear":
        console.clear()
        console.print(render_banner(session))

    else:
        console.print(f"[dim]Unknown command: {command}. Type /help for available commands.[/]")

    return True


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CodeX TUI — Rich Terminal Interface")
    parser.add_argument("--mode", choices=["agent", "plan", "yolo"], default=None)
    parser.add_argument("--policy", choices=["auto", "suggest", "never"], default=None)
    parser.add_argument("--workspace", "-w", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--version", "-v", action="store_true")
    args = parser.parse_args()

    if args.version:
        from codex import __version__
        console.print(f"CodeX v{__version__}")
        return

    if not check_api_key():
        sys.exit(1)

    session_config = SessionConfig(
        mode=Mode(args.mode) if args.mode else None,
        approval_policy=ApprovalPolicy(args.policy) if args.policy else None,
        workspace=Path(args.workspace).resolve() if args.workspace else None,
        model=args.model,
    )

    session = Session(session_config=session_config)
    session.initialize()

    run_tui(session)


if __name__ == "__main__":
    main()
