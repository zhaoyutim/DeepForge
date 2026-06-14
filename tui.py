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
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

from deepforge.config import ApprovalPolicy, Mode
from deepforge.session import Session, SessionConfig
from deepforge.agent import AgentResponse
import themes

console = Console()
_READLINE_READY = False


# ── Helpers ────────────────────────────────────────────────────────

def check_api_key() -> bool:
    if not (
        os.environ.get("DEEPFORGE_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("CODEX_API_KEY")
    ):
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


# ── Interactive Loop ───────────────────────────────────────────────

def run_tui(session: Session, *, theme_name: str = "default") -> None:
    """Main TUI loop with streaming output."""
    # Activate the selected theme
    try:
        themes.activate(theme_name)
    except ValueError:
        console.print(f"[yellow]⚠ Theme '{theme_name}' not found, using default[/]")
        themes.activate("default")
        theme_name = "default"

    active = themes.get_active()
    console.clear()
    console.print(active.render_banner(session))

    while True:
        try:
            bar = active.render_status_bar(session)
            console.print(bar)
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

        # ── Streaming response ─────────────────────────────────
        console.print()
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
                    if not success and event.get("output"):
                        console.print(f"[red]{event['output']}[/]")

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
    parser.add_argument("--model", default=None)
    parser.add_argument("--mcp-config", default=None,
                        help="MCP config path (default: ~/.deepforge/mcp.yaml)")
    parser.add_argument("--no-mcp", action="store_true",
                        help="Disable MCP integration for this session")
    parser.add_argument("--version", "-v", action="store_true")
    parser.add_argument("--theme", default="default",
                        help="Visual theme (default, worldcup)")
    args = parser.parse_args()

    if args.version:
        from deepforge import __version__
        console.print(f"DeepForge v{__version__}")
        return

    if not check_api_key():
        sys.exit(1)

    session_config = SessionConfig(
        mode=Mode(args.mode) if args.mode else None,
        approval_policy=ApprovalPolicy(args.policy) if args.policy else None,
        workspace=Path(args.workspace).resolve() if args.workspace else None,
        model=args.model,
        mcp_enabled=not args.no_mcp,
        mcp_config_path=Path(args.mcp_config).expanduser() if args.mcp_config else None,
        approval_callback=create_tui_approval_callback(),
    )

    session = Session(session_config=session_config)
    try:
        session.initialize()
        run_tui(session, theme_name=args.theme)
    finally:
        session.close()


if __name__ == "__main__":
    main()
