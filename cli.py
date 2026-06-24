#!/usr/bin/env python3
"""
DeepForge CLI — interactive terminal interface.

Usage:
    python cli.py                              # Start interactive session
    python cli.py --backend azure               # Use Azure backend
    python cli.py --config config/env.yaml      # Explicit config path
    python cli.py --mode yolo                   # Start in YOLO mode
    python cli.py --mode plan                   # Start in Plan mode (read-only)
    python cli.py -c "read README.md"           # One-shot command
    python cli.py --workspace /path/to/dir      # Set workspace

Environment:
    DEEPSEEK_API_KEY        DeepSeek API key (required for deepseek backend)
    AZURE_OPENAI_API_KEY    Azure OpenAI API key (required for azure backend)
    DEEPFORGE_MODE          Default mode (agent/plan/yolo)
    DEEPFORGE_APPROVAL      Default approval policy (auto/suggest/never)
    DEEPFORGE_BACKEND       Default backend (deepseek/azure)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add parent directory to path for development use
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deepforge.config import ApprovalPolicy, Backend, Mode, config
from deepforge.session import Session, SessionConfig


def check_api_key(backend: str = "deepseek", yaml_data: dict | None = None) -> bool:
    """Check if the API key is configured for the given backend."""
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
        env_example = "export AZURE_OPENAI_API_KEY='your-key-here'"
        get_key_url = "https://portal.azure.com"
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
        env_example = "export DEEPSEEK_API_KEY='your-key-here'"
        get_key_url = "https://platform.deepseek.com"

    if not key:
        print("╔══════════════════════════════════════════════════════════╗")
        print(f"║  {key_name} not found!{' ' * (54 - len(key_name))}║")
        print("║                                                        ║")
        print(f"║  Set the environment variable:                         ║")
        print(f"║    {env_example:<54}║")
        print("║                                                        ║")
        print(f"║  Get your API key at: {get_key_url:<32}║")
        print("║                                                        ║")
        print("║  Or configure via config/env.yaml                      ║")
        print("╚══════════════════════════════════════════════════════════╝")
        return False
    return True


def print_banner(session: Session):
    """Print the welcome banner."""
    mode_str = session.mode.value.upper()
    policy_str = session.policy.value.upper()
    backend_str = config.backend.value.upper()
    tools = session.available_tools if session.available_tools else []

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║                    DeepForge v0.1.0                      ║")
    print("║              CodeWhale Architecture in Python            ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  Mode: {mode_str:<8}  Policy: {policy_str:<8}  Backend: {backend_str:<8} ║")
    print(f"║  Tools: {len(tools):<2} available                                   ║")
    print(f"║  Workspace: {str(session.workspace):<45} ║"[:64] + "║")
    print("╠══════════════════════════════════════════════════════════╣")
    print("║  Commands:                                              ║")
    print("║    /mode agent|plan|yolo   Switch operating mode        ║")
    print("║    /policy auto|suggest    Change approval policy       ║")
    print("║    /tools                  List available tools          ║")
    print("║    /mcp status|tools|reload Manage MCP servers           ║")
    print("║    /stats                  Show session statistics       ║")
    print("║    /compact                Compact context window        ║")
    print("║    /help                   Show this help                ║")
    print("║    /exit, /quit            Exit DeepForge                ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


def _format_tool_arguments(arguments: dict) -> str:
    text = json.dumps(arguments or {}, ensure_ascii=False, indent=2)
    if len(text) > 1000:
        return text[:1000] + "\n... (truncated)"
    return text


def create_cli_approval_callback():
    """Create an interactive approval callback for suggest-mode tools."""

    def approve(tool, tool_call, gate_result) -> bool:
        print()
        print("Approval required")
        print(f"  Tool: {tool_call.function_name}")
        print(f"  Reason: {gate_result.reason}")
        if tool.is_shell:
            print("  Type: shell")
        elif tool.is_write:
            print("  Type: write")
        elif tool.requires_approval:
            print("  Type: approval-required")
        print("  Arguments:")
        print(_format_tool_arguments(tool_call.arguments))
        answer = input("Approve this tool call? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    return approve


def print_mcp_status(session: Session) -> None:
    status = session.mcp_status()
    print(f"MCP enabled: {status.get('enabled')}")
    if status.get("config_path"):
        print(f"Config: {status['config_path']}")
    if status.get("error"):
        print(f"Error: {status['error']}")
    servers = status.get("servers") or []
    if not servers:
        print("No MCP servers configured or connected.")
        return
    for server in servers:
        state = "connected" if server.get("connected") else "error"
        print(
            f"- {server.get('name')} [{server.get('transport')}] {state} "
            f"tools={server.get('tool_count', 0)} "
            f"resources={server.get('resource_count', 0)} "
            f"prompts={server.get('prompt_count', 0)}"
        )
        if server.get("error"):
            print(f"  error: {server['error']}")


def handle_mcp_command(parts: list[str], session: Session) -> None:
    subcommand = parts[1].lower() if len(parts) > 1 else "status"
    if subcommand == "status":
        print_mcp_status(session)
    elif subcommand == "tools":
        tools = [name for name in session.available_tools if name.startswith("mcp__")]
        if not tools:
            print("No MCP tools registered.")
            return
        print(f"MCP tools ({len(tools)}):")
        for name in tools:
            print(f"  - {name}")
    elif subcommand == "reload":
        session.reload_mcp()
        print("MCP reloaded.")
        print_mcp_status(session)
    else:
        print("Usage: /mcp status|tools|reload")


def handle_command(cmd: str, session: Session) -> bool:
    """
    Handle a slash command.

    Returns True if the session should continue, False to exit.
    """
    parts = cmd.strip().split()
    command = parts[0].lower()

    if command in ("/exit", "/quit", "/q"):
        print("Goodbye! 👋")
        return False

    elif command == "/help":
        print_banner(session)

    elif command == "/mode":
        if len(parts) < 2:
            print(f"Current mode: {session.mode.value}")
            print("Usage: /mode agent|plan|yolo")
        else:
            try:
                new_mode = Mode(parts[1].lower())
                session.set_mode(new_mode)
                print(f"✅ Mode changed to: {new_mode.value}")
            except ValueError:
                print(f"❌ Invalid mode: {parts[1]}. Use agent, plan, or yolo.")

    elif command == "/policy":
        if len(parts) < 2:
            print(f"Current policy: {session.policy.value}")
            print("Usage: /policy auto|suggest|never")
        else:
            try:
                new_policy = ApprovalPolicy(parts[1].lower())
                session.set_approval_policy(new_policy)
                print(f"✅ Policy changed to: {new_policy.value}")
            except ValueError:
                print(f"❌ Invalid policy: {parts[1]}. Use auto, suggest, or never.")

    elif command == "/tools":
        tools = session.available_tools
        if tools:
            print(f"Available tools ({len(tools)}):")
            for t in tools:
                print(f"  - {t}")
        else:
            print("No tools available (session not initialized).")

    elif command == "/mcp":
        handle_mcp_command(parts, session)

    elif command == "/stats":
        stats = session.stats()
        print("Session Statistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")

    elif command == "/context":
        ctx_stats = session.get_context_stats()
        if ctx_stats:
            print("Context Window:")
            for key, value in ctx_stats.items():
                print(f"  {key}: {value}")
        else:
            print("Context not initialized.")

    elif command == "/compact":
        result = session.compact()
        if result.get("compacted"):
            print(f"✅ Compaction complete: freed {result.get('tokens_freed', 0):,} tokens")
        else:
            print(f"ℹ️  {result.get('reason', 'Compaction not needed')}")

    else:
        print(f"Unknown command: {command}. Type /help for available commands.")

    return True


def interactive_mode(session: Session):
    """Run the interactive REPL."""
    print_banner(session)

    while True:
        try:
            user_input = input("deepforge> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye! 👋")
            break

        if not user_input:
            continue

        # Check for slash commands
        if user_input.startswith("/"):
            if not handle_command(user_input, session):
                break
            continue

        # Process through agent with streaming
        print()  # blank line before response
        tool_count = 0
        try:
            for event in session.agent.process_stream(user_input):
                etype = event["type"]
                if etype == "text":
                    print(event["content"], end="", flush=True)
                elif etype == "tool_start":
                    tool_count += 1
                elif etype == "tool_end":
                    if not event.get("success", False):
                        output = event.get("output") or "Tool failed"
                        print(f"\n✗ {event.get('name', 'tool')}: {output}")
                elif etype == "done":
                    latency = event.get("ms", 0)
                    tokens = event.get("tokens", 0)
                    parts = []
                    if tool_count:
                        parts.append(f"{tool_count} tool(s)")
                    parts.append(f"{latency:.0f}ms")
                    parts.append(f"{tokens:,} tokens")
                    print(f"\n── {' · '.join(parts)} ──")
                elif etype == "error":
                    print(f"\n❌ {event['error']}")
        except Exception as e:
            print(f"❌ Error: {e}")
        print()  # blank line after response


def one_shot_mode(session: Session, command: str):
    """Run a single command and exit."""
    try:
        response = session.send(command)
        print(response.content)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="DeepForge — CodeWhale Architecture in Python with DeepSeek API",
    )
    parser.add_argument(
        "-c", "--command",
        help="One-shot command (non-interactive mode)",
    )
    parser.add_argument(
        "--mode",
        choices=["agent", "plan", "yolo"],
        default=None,
        help="Operating mode (default: agent)",
    )
    parser.add_argument(
        "--policy",
        choices=["auto", "suggest", "never"],
        default=None,
        help="Approval policy (default: suggest)",
    )
    parser.add_argument(
        "--workspace", "-w",
        default=None,
        help="Workspace directory (default: current directory)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model to use (default: deepseek-chat for DeepSeek, gpt5.4 for Azure)",
    )
    parser.add_argument(
        "--backend", "-b",
        choices=["deepseek", "azure"],
        default=None,
        help="Model backend: deepseek or azure (default: deepseek)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to env.yaml config file (default: auto-discover config/env.yaml)",
    )
    parser.add_argument(
        "--mcp-config",
        default=None,
        help="MCP config path (default: ~/.deepforge/mcp.yaml)",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Disable MCP integration for this session",
    )
    parser.add_argument(
        "--version", "-v",
        action="store_true",
        help="Show version and exit",
    )
    args = parser.parse_args()

    if args.version:
        from deepforge import __version__
        print(f"DeepForge v{__version__}")
        return

    # Determine backend (CLI arg > config file > env var > default)
    resolved_backend = args.backend
    config_path = Path(args.config).resolve() if args.config else None
    from deepforge.config import _discover_config_path, _load_yaml_config
    yaml_data = _load_yaml_config(config_path or _discover_config_path())
    if resolved_backend is None:
        # Try to load YAML to get backend early (for API key check)
        resolved_backend = yaml_data.get("backend", "deepseek")
    resolved_backend = resolved_backend.lower()

    # Check API key for resolved backend
    if not check_api_key(resolved_backend, yaml_data):
        sys.exit(1)

    # Build session config
    session_config = SessionConfig(
        mode=Mode(args.mode) if args.mode else None,
        approval_policy=ApprovalPolicy(args.policy) if args.policy else None,
        workspace=Path(args.workspace).resolve() if args.workspace else None,
        model=args.model,
        backend=args.backend,
        config_path=Path(args.config).resolve() if args.config else None,
        mcp_enabled=not args.no_mcp,
        mcp_config_path=Path(args.mcp_config).expanduser() if args.mcp_config else None,
        approval_callback=None if args.command else create_cli_approval_callback(),
    )

    # Load YAML config into global config before session initialization
    if session_config.config_path or not args.backend:
        config_path = session_config.config_path or None
        from deepforge.config import Config
        yaml_config = Config.from_yaml(config_path)
        # Merge CLI overrides into global config
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
        # Replace global config
        config.__dict__.update(yaml_config.__dict__)

    # Create and initialize session
    session = Session(session_config=session_config)
    try:
        session.initialize()

        # Run
        if args.command:
            one_shot_mode(session, args.command)
        else:
            interactive_mode(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
