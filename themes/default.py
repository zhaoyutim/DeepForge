"""
Default DeepForge theme — cyan tech aesthetic, clean panels, compact status bar.
"""

from themes import Theme, register
from deepforge.config import ApprovalPolicy, Backend, Mode


# ── Colors ────────────────────────────────────────────────────────────

COLORS = {
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

# ── Helpers (theme-aware) ─────────────────────────────────────────────

def _mode_color(mode: Mode) -> str:
    return {
        Mode.AGENT: COLORS["mode_agent"],
        Mode.PLAN: COLORS["mode_plan"],
        Mode.YOLO: COLORS["mode_yolo"],
    }.get(mode, "white")


def _policy_color(policy: ApprovalPolicy) -> str:
    return {
        ApprovalPolicy.AUTO: COLORS["policy_auto"],
        ApprovalPolicy.SUGGEST: COLORS["policy_suggest"],
        ApprovalPolicy.NEVER: COLORS["policy_never"],
    }.get(policy, "white")


def _pressure_color(pressure: str) -> str:
    return {
        "low": COLORS["context_low"],
        "medium": COLORS["context_medium"],
        "high": COLORS["context_high"],
        "critical": COLORS["context_critical"],
    }.get(pressure, "white")


def _pressure_bar(pressure: str, ratio: float) -> "Text":
    from rich.text import Text

    bar_width = 10
    filled = int(ratio * bar_width)
    empty = bar_width - filled
    color = _pressure_color(pressure)
    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * empty, style="dim")
    bar.append(f" {ratio:.0%}", style="dim")
    return bar


# ── Banner ────────────────────────────────────────────────────────────

def _term_width() -> int:
    import shutil
    return shutil.get_terminal_size().columns


def render_banner(session) -> "Panel":
    from rich.panel import Panel
    from rich.text import Text

    mode_str = session.mode.value.upper()
    policy_str = session.policy.value.upper()
    from deepforge.config import config
    backend_str = config.backend.value.upper()

    # Responsive width: cap at 92, shrink for narrow terminals
    tw = _term_width()
    w = max(40, min(92, tw - 4))  # box content width
    inner_w = w - 2                # usable width inside borders

    # Build horizontal borders
    h_line = "─" * inner_w

    # Truncate workspace to fit
    workspace_raw = str(session.workspace)
    # Reserve space for "Tools: XX  Workspace: " prefix (~18 chars) + gap
    ws_max = inner_w - 30
    if len(workspace_raw) > ws_max and ws_max > 10:
        workspace_str = "..." + workspace_raw[-(ws_max - 3):]
    else:
        workspace_str = workspace_raw

    text = Text()

    # Top border
    text.append(f"┌{h_line}┐\n", style="dim cyan")

    # Title line — centered
    title = "DeepForge v0.1.0"
    pad_total = inner_w - len(title)
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    text.append("│", style="dim cyan")
    text.append(" " * pad_l, style="")
    text.append(title, style=COLORS["banner"])
    text.append(" " * pad_r, style="")
    text.append("│\n", style="dim cyan")

    # Subtitle
    subtitle = "CodeWhale Architecture in Python"
    pad_total = inner_w - len(subtitle)
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    text.append("│", style="dim cyan")
    text.append(" " * pad_l, style="")
    text.append(subtitle, style="cyan")
    text.append(" " * pad_r, style="")
    text.append("│\n", style="dim cyan")

    # Separator
    text.append(f"├{h_line}┤\n", style="dim cyan")

    # Mode / Policy / Backend line
    mode_str_full = f"Mode: {mode_str}"
    policy_str_full = f"Policy: {policy_str}"
    backend_str_full = f"Backend: {backend_str}"
    line1 = f"  {mode_str_full:<14}  {policy_str_full:<16}  {backend_str_full}"
    # Pad to inner_w
    line1 = line1[:inner_w].ljust(inner_w)
    text.append("│", style="dim cyan")
    text.append(line1[:16], style="dim cyan")
    text.append(line1[16:], style="")
    text.append("│\n", style="dim cyan")

    # Tools / Workspace line
    tools_str = f"Tools: {len(session.available_tools)}"
    ws_str = f"Workspace: {workspace_str}"
    line2 = f"  {tools_str}  {ws_str}"
    line2 = line2[:inner_w].ljust(inner_w)
    text.append("│", style="dim cyan")
    text.append(line2, style="dim")
    text.append("│\n", style="dim cyan")

    # Context window / Reasoning effort line (Azure-specific)
    from deepforge.config import Backend as _Backend
    if config.backend == _Backend.AZURE:
        ctx_tokens = config.azure_context_tokens
        ctx_k = f"{ctx_tokens // 1024}K"
        effort = getattr(session.agent.client, "reasoning_effort", None) if session.agent and session.agent.client else None
        parts = f"Context: {ctx_k}"
        if effort:
            parts += f"  Eff: {effort}"
        line3 = f"  {parts}"
        line3 = line3[:inner_w].ljust(inner_w)
        text.append("│", style="dim cyan")
        text.append(line3, style="dim magenta")
        text.append("│\n", style="dim cyan")

    # Separator
    text.append(f"├{h_line}┤\n", style="dim cyan")

    # Commands line
    if inner_w >= 50:
        cmds = "/mode /policy /tools /mcp /stats /compact /help /exit /theme"
    else:
        cmds = "/mode /policy /tools /mcp /help /exit"
    cmd_line = f"  {cmds}"
    cmd_line = cmd_line[:inner_w].ljust(inner_w)
    text.append("│", style="dim cyan")
    text.append(cmd_line, style="dim")
    text.append("│\n", style="dim cyan")

    # Bottom border
    text.append(f"└{h_line}┘", style="dim cyan")

    return Panel(text, border_style="cyan")


# ── Status Bar ────────────────────────────────────────────────────────

def render_status_bar(session) -> "Text":
    from rich.text import Text

    ctx_stats = session.get_context_stats()
    pressure = ctx_stats.get("pressure", "low")
    ratio_str = ctx_stats.get("usage_ratio", "0%")
    ratio = float(ratio_str.rstrip("%")) / 100 if ratio_str else 0.0

    from deepforge.config import Backend, config
    is_azure = config.backend == Backend.AZURE

    bar = Text()
    bar.append(f" {session.mode.value.upper()} ", style=_mode_color(session.mode))
    bar.append(f" {session.policy.value.upper()} ", style=_policy_color(session.policy))
    bar.append(f" {config.backend.value.upper()} ", style="dim cyan")
    # Show reasoning effort on Azure (or any backend that has it set)
    effort = getattr(session.agent.client, "reasoning_effort", None) if session.agent and session.agent.client else None
    if effort:
        bar.append(f"🧠{effort} ", style="bold magenta")
    bar.append("  Context: ", style="dim")
    bar.append_text(_pressure_bar(pressure, ratio))
    bar.append(" ")
    bar.append(f"  Tools: {len(session.available_tools)}", style="dim")
    return bar


# ── CodeWhale Layout Colors ──────────────────────────────────────────

CODEWHALE_COLORS = {
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


# ── Register ──────────────────────────────────────────────────────────

theme = Theme(
    name="default",
    label="Default — cyan tech aesthetic",
    colors=COLORS,
    codewhale_colors=CODEWHALE_COLORS,
    render_banner=render_banner,
    render_status_bar=render_status_bar,
)
register(theme)
