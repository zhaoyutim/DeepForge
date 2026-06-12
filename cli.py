#!/usr/bin/env python3
"""
CodeX CLI — interactive terminal interface.

Usage:
    python cli.py                          # Start interactive session
    python cli.py --mode yolo              # Start in YOLO mode
    python cli.py --mode plan               # Start in Plan mode (read-only)
    python cli.py -c "read README.md"       # One-shot command
    python cli.py --workspace /path/to/dir  # Set workspace

Environment:
    DEEPSEEK_API_KEY    DeepSeek API key (required)
    CODEX_MODE          Default mode (agent/plan/yolo)
    CODEX_APPROVAL      Default approval policy (auto/suggest/never)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add parent directory to path for development use
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex.config import ApprovalPolicy, Mode, config
from codex.session import Session, SessionConfig


def check_api_key() -> bool:
    """Check if the DeepSeek API key is configured."""
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CODEX_API_KEY")
    if not key:
        print("╔══════════════════════════════════════════════════════════╗")
        print("║  DeepSeek API key not found!                            ║")
        print("║                                                        ║")
        print("║  Set the DEEPSEEK_API_KEY environment variable:         ║")
        print("║    export DEEPSEEK_API_KEY='your-key-here'              ║")
        print("║                                                        ║")
        print("║  Get your API key at: https://platform.deepseek.com     ║")
        print("╚══════════════════════════════════════════════════════════╝")
        return False
    return True


def print_banner(session: Session):
    """Print the welcome banner."""
    mode_str = session.mode.value.upper()
    policy_str = session.policy.value.upper()
    tools = session.available_tools if session.available_tools else []

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║                    CodeX v0.1.0                          ║")
    print("║              CodeWhale Architecture in Python            ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  Mode: {mode_str:<8}  Policy: {policy_str:<8}                        ║")
    print(f"║  Tools: {len(tools):<2} available                                   ║")
    print(f"║  Workspace: {str(session.workspace):<45} ║"[:64] + "║")
    print("╠══════════════════════════════════════════════════════════╣")
    print("║  Commands:                                              ║")
    print("║    /mode agent|plan|yolo   Switch operating mode        ║")
    print("║    /policy auto|suggest    Change approval policy       ║")
    print("║    /tools                  List available tools          ║")
    print("║    /stats                  Show session statistics       ║")
    print("║    /compact                Compact context window        ║")
    print("║    /help                   Show this help                ║")
    print("║    /exit, /quit            Exit CodeX                    ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


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
            user_input = input("codex> ").strip()
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
                    pass  # silent
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
        description="CodeX — CodeWhale Architecture in Python with DeepSeek API",
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
        help="DeepSeek model to use (default: deepseek-chat)",
    )
    parser.add_argument(
        "--version", "-v",
        action="store_true",
        help="Show version and exit",
    )
    args = parser.parse_args()

    if args.version:
        from codex import __version__
        print(f"CodeX v{__version__}")
        return

    # Check API key
    if not check_api_key():
        sys.exit(1)

    # Build session config
    session_config = SessionConfig(
        mode=Mode(args.mode) if args.mode else None,
        approval_policy=ApprovalPolicy(args.policy) if args.policy else None,
        workspace=Path(args.workspace).resolve() if args.workspace else None,
        model=args.model,
    )

    # Create and initialize session
    session = Session(session_config=session_config)
    session.initialize()

    # Run
    if args.command:
        one_shot_mode(session, args.command)
    else:
        interactive_mode(session)


if __name__ == "__main__":
    main()
