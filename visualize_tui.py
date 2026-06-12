#!/usr/bin/env python3
"""Offline Rich visualization for the CodeX TUI architecture.

This script renders a dashboard-style preview of how the interactive TUI
connects to the core CodeX subsystems. It intentionally stays below the model
layer, so it can run without a DeepSeek API key while still exercising the real
tool registry, approval gate, context window, and dispatcher.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

from codex.approval.gate import ApprovalGate, GateDecision
from codex.config import ApprovalPolicy, Mode, config
from codex.context.window import ContextWindow
from codex.dispatch.dispatcher import DispatchResult, ToolDispatcher
from codex.tools.base import BaseTool, ToolRegistry
from codex.tools.file_tools import EditFileTool, ListDirectoryTool, ReadFileTool, WriteFileTool
from codex.tools.git_tools import GitDiffTool, GitLogTool, GitStatusTool
from codex.tools.search_tools import FetchUrlTool, FileSearchTool, GrepFilesTool, WebSearchTool
from codex.tools.shell_tools import ExecShellTool
from codex.types import Message, ToolCall, Turn


console = Console()

MODE_STYLES = {
    Mode.AGENT: "bold green",
    Mode.PLAN: "bold yellow",
    Mode.YOLO: "bold red",
}

POLICY_STYLES = {
    ApprovalPolicy.AUTO: "green",
    ApprovalPolicy.SUGGEST: "yellow",
    ApprovalPolicy.NEVER: "red",
}

PRESSURE_STYLES = {
    "low": "green",
    "medium": "yellow",
    "high": "orange1",
    "critical": "red",
}

SYSTEM_PROMPT_TEMPLATE = """You are CodeX, an AI coding agent running on DeepSeek.

Current workspace: {workspace}
Current mode: {mode}
Approval policy: {policy}
Context usage: {context_usage}

Be direct and efficient. Execute, don't just describe."""


def register_visualizer_tools(registry: ToolRegistry) -> ToolRegistry:
    tools = [
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListDirectoryTool(),
        GrepFilesTool(),
        FileSearchTool(),
        WebSearchTool(),
        FetchUrlTool(),
        ExecShellTool(),
        GitStatusTool(),
        GitDiffTool(),
        GitLogTool(),
    ]
    registry.register_many(tools)
    return registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render an offline visualization of the CodeX TUI core flow.",
    )
    parser.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.AGENT.value)
    parser.add_argument(
        "--policy",
        choices=[p.value for p in ApprovalPolicy],
        default=ApprovalPolicy.SUGGEST.value,
    )
    parser.add_argument("--workspace", "-w", default=".")
    parser.add_argument("--no-demo", action="store_true", help="Skip the live dispatcher demo")
    return parser.parse_args()


def build_registry(workspace: Path) -> ToolRegistry:
    config.workspace = workspace
    registry = ToolRegistry()
    register_visualizer_tools(registry)
    return registry


def build_context(workspace: Path, mode: Mode, policy: ApprovalPolicy) -> ContextWindow:
    context = ContextWindow(max_tokens=8_000)
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        workspace=str(workspace),
        mode=mode.value,
        policy=policy.value,
        context_usage="0%",
    )
    context.set_system_prompt(prompt)
    context.add_turn(
        Turn(
            user_message=Message.user("Review this repo and visualize the TUI application."),
            assistant_message=Message.assistant(
                "Mapped Session, Agent, Dispatcher, Approval Gate, Context Window, and Tools."
            ),
        )
    )
    return context


def header_panel(workspace: Path, mode: Mode, policy: ApprovalPolicy, registry: ToolRegistry) -> Panel:
    title = Text("CodeX TUI Visualizer", style="bold cyan")
    subtitle = Text("offline core-flow preview", style="dim")

    table = Table.grid(expand=True)
    table.add_column(justify="center")
    table.add_row(title)
    table.add_row(subtitle)
    table.add_row("")
    table.add_row(
        Text.assemble(
            ("Mode ", "dim"),
            (mode.value.upper(), MODE_STYLES[mode]),
            ("   Policy ", "dim"),
            (policy.value.upper(), POLICY_STYLES[policy]),
            ("   Tools ", "dim"),
            (str(registry.count), "bold cyan"),
        )
    )
    table.add_row(Text(str(workspace), style="dim"))
    return Panel(table, border_style="cyan", box=box.DOUBLE)


def context_panel(context: ContextWindow) -> Panel:
    stats = context.stats()
    pressure = stats["pressure"]
    ratio = context.usage_ratio

    progress = Progress(
        TextColumn("[dim]context[/]"),
        BarColumn(bar_width=28, complete_style=PRESSURE_STYLES[pressure]),
        TextColumn(f"{ratio:.1%}"),
        expand=False,
    )
    progress.add_task("context", total=100, completed=ratio * 100)

    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Pressure", f"[{PRESSURE_STYLES[pressure]}]{pressure.upper()}[/]")
    table.add_row("Used", f"{stats['used_tokens']:,} / {stats['max_tokens']:,} tokens")
    table.add_row("Active turns", str(stats["active_turns"]))
    table.add_row("Compaction", "needed" if stats["needs_compaction"] else "not needed")

    return Panel(Group(progress, table), title="Context Window", border_style=PRESSURE_STYLES[pressure])


def status_panel(workspace: Path, registry: ToolRegistry, mode: Mode, policy: ApprovalPolicy) -> Panel:
    api_state = "configured" if config.api_key else "offline demo"
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Workspace", str(workspace.name or workspace))
    table.add_row("Model layer", api_state)
    table.add_row("Read tools", str(len(registry.get_read_tools())))
    table.add_row("Write tools", str(len(registry.get_write_tools())))
    table.add_row("Mode", f"[{MODE_STYLES[mode]}]{mode.value}[/]")
    table.add_row("Policy", f"[{POLICY_STYLES[policy]}]{policy.value}[/]")
    return Panel(table, title="Session Surface", border_style="green")


def pipeline_panel() -> Panel:
    steps = [
        ("Prompt", "User input lands in CLI/TUI"),
        ("Session", "Mode, policy, workspace"),
        ("Agent", "Model loop and tool calls"),
        ("Gate", "Allow, prompt, or block"),
        ("Dispatch", "Parallel tool execution"),
        ("Context", "Token pressure and compaction"),
    ]

    cards = []
    for index, (name, detail) in enumerate(steps, start=1):
        body = Text.assemble((f"{index}. {name}\n", "bold cyan"), (detail, "white"))
        cards.append(Panel(body, border_style="dim", box=box.ROUNDED, width=22))
    return Panel(Columns(cards, equal=True, expand=True), title="TUI Core Flow", border_style="cyan")


def tool_inventory_panel(registry: ToolRegistry) -> Panel:
    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Tool", style="cyan")
    table.add_column("Flags", justify="center")
    table.add_column("Purpose")

    for name in registry.tool_names:
        tool = registry.get(name)
        if tool is None:
            continue
        flags = []
        if tool.is_read:
            flags.append("read")
        if tool.is_write:
            flags.append("write")
        if tool.is_shell:
            flags.append("shell")
        if tool.is_network:
            flags.append("net")
        table.add_row(name, ", ".join(flags) or "-", tool.description)

    return Panel(table, title="Registered Tools", border_style="blue")


def decision_style(decision: GateDecision) -> str:
    return {
        GateDecision.ALLOW: "green",
        GateDecision.PROMPT: "yellow",
        GateDecision.BLOCK: "red",
    }[decision]


def approval_matrix_panel(registry: ToolRegistry, mode: Mode, policy: ApprovalPolicy) -> Panel:
    representative_names = ["read_file", "write_file", "exec_shell", "fetch_url"]
    tools = [registry.get(name) for name in representative_names]
    gate = ApprovalGate(mode=mode, policy=policy)

    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Tool")
    table.add_column("Kind")
    table.add_column("Decision", justify="center")
    table.add_column("Reason")

    for tool in tools:
        if tool is None:
            continue
        result = gate.check(tool)
        kind = describe_tool_kind(tool)
        style = decision_style(result.decision)
        table.add_row(
            tool.name,
            kind,
            f"[{style}]{result.decision.value.upper()}[/]",
            result.reason,
        )

    title = f"Approval Gate: {mode.value} / {policy.value}"
    return Panel(table, title=title, border_style="yellow")


def describe_tool_kind(tool: BaseTool) -> str:
    if tool.is_shell:
        return "shell"
    if tool.is_write:
        return "write"
    if tool.is_network:
        return "network read"
    if tool.is_read:
        return "read"
    return "custom"


def summarize_result(content: str) -> str:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "(empty)")
    if len(first_line) > 86:
        return first_line[:83] + "..."
    return first_line


def demo_calls() -> list[ToolCall]:
    return [
        ToolCall(id="demo-list", function_name="list_dir", arguments={"path": "."}),
        ToolCall(
            id="demo-read",
            function_name="read_file",
            arguments={"path": "README.md", "start_line": 1, "max_lines": 12},
        ),
        ToolCall(id="demo-search", function_name="file_search", arguments={"query": "tui", "limit": 8}),
        ToolCall(
            id="demo-grep",
            function_name="grep_files",
            arguments={"pattern": "def handle_command", "path": ".", "max_results": 4},
        ),
    ]


def run_dispatch_demo(registry: ToolRegistry, mode: Mode, policy: ApprovalPolicy) -> Panel:
    gate = ApprovalGate(mode=mode, policy=policy)
    dispatcher = ToolDispatcher(registry)
    calls = demo_calls()
    allowed_calls = []
    decisions: dict[str, GateDecision] = {}

    for call in calls:
        tool = registry.get(call.function_name)
        if tool is None:
            continue
        decision = gate.check(tool, call).decision
        decisions[call.id] = decision
        if decision == GateDecision.ALLOW:
            allowed_calls.append(call)

    if allowed_calls:
        result = dispatcher.dispatch(allowed_calls)
    else:
        result = DispatchResult([], 0.0, 0, 0)

    result_by_id = {tool_result.tool_call_id: tool_result for tool_result in result.tool_results}
    table = Table(box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Call", style="cyan")
    table.add_column("Gate", justify="center")
    table.add_column("Result", justify="center")
    table.add_column("Summary")

    for call in calls:
        decision = decisions.get(call.id, GateDecision.BLOCK)
        tool_result = result_by_id.get(call.id)
        gate_style = decision_style(decision)
        if tool_result:
            result_text = "OK" if tool_result.success else "ERR"
            result_style = "green" if tool_result.success else "red"
            summary = summarize_result(tool_result.content)
        else:
            result_text = "SKIP"
            result_style = "dim"
            summary = "Not dispatched by the approval gate"
        table.add_row(
            call.function_name,
            f"[{gate_style}]{decision.value.upper()}[/]",
            f"[{result_style}]{result_text}[/]",
            summary,
        )

    footer = Text.assemble(
        ("parallel executions: ", "dim"),
        (str(result.parallel_executions), "bold cyan"),
        ("   sequential executions: ", "dim"),
        (str(result.sequential_executions), "bold cyan"),
        ("   elapsed: ", "dim"),
        (f"{result.total_time_ms:.1f}ms", "bold cyan"),
    )
    return Panel(Group(table, Align.right(footer)), title="Live Core Demo", border_style="magenta")


def render_visualization(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).expanduser().resolve()
    mode = Mode(args.mode)
    policy = ApprovalPolicy(args.policy)
    registry = build_registry(workspace)
    context = build_context(workspace, mode, policy)

    console.clear()
    console.print(header_panel(workspace, mode, policy, registry))
    console.print(Columns([status_panel(workspace, registry, mode, policy), context_panel(context)], equal=True))
    console.print(pipeline_panel())
    console.print(Columns([approval_matrix_panel(registry, mode, policy), tool_inventory_panel(registry)], equal=True))

    if not args.no_demo:
        console.print(run_dispatch_demo(registry, mode, policy))


def main() -> None:
    render_visualization(parse_args())


if __name__ == "__main__":
    main()