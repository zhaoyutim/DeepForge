"""
🐉 Dragon Ball Theme for DeepForge

Dragon Ball Z inspired TUI theme with:
  - Goku orange / Vegeta blue color scheme
  - Shenron green accents
  - Dragon Ball gold highlights
  - Super Saiyan aura effects
  - 7-star Dragon Ball banner
  - Character-based mode indicators

Usage:
    /theme dragonball      — activate and show dashboard
    python tui.py --theme dragonball
"""

from themes import Theme, register
from deepforge.config import ApprovalPolicy, Backend, Mode

# ── Dragon Ball Color Palette ─────────────────────────────────────────

COLORS = {
    # Core character colors
    "goku": "#FF6B35",
    "goku_bold": "bold #FF6B35",
    "vegeta": "#1A237E",
    "vegeta_bold": "bold #1A237E",
    "shenron": "#2E7D32",
    "shenron_bold": "bold #2E7D32",
    "dragonball": "#FFD700",
    "dragonball_bold": "bold #FFD700",
    "frieza": "#9C27B0",
    "buu": "#FF4081",
    "trunks": "#00BCD4",
    "gohan": "#FF9800",
    "piccolo": "#4CAF50",

    # Banner
    "banner": "bold #FF6B35",
    "banner_border": "bold #FFD700",
    "banner_text": "bold #FFD700",
    "banner_accent": "#FF6B35",

    # Mode colors (character-themed)
    "mode_agent": "bold #FF6B35",      # Goku orange
    "mode_plan": "bold #1A237E",       # Vegeta blue
    "mode_yolo": "bold #FF4081",       # Buu pink (dangerous!)

    # Policy colors
    "policy_auto": "#2E7D32",          # Shenron green
    "policy_suggest": "#FFD700",       # Dragon ball gold
    "policy_never": "#FF4081",         # Buu pink

    # Context pressure
    "context_low": "#2E7D32",          # Shenron green
    "context_medium": "#FFD700",       # Dragon ball gold
    "context_high": "#FF6B35",         # Goku orange
    "context_critical": "#FF4081",     # Buu pink

    # General
    "tool": "dim #00BCD4",
    "response": "white",
    "error": "bold #FF4081",
    "success": "bold #2E7D32",
    "timestamp": "dim",
    "separator": "dim #FFD700",
    "prompt": "bold #FFD700",
    "dim": "dim",
}


# ── Helpers ───────────────────────────────────────────────────────────

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


def _term_width() -> int:
    import shutil
    return shutil.get_terminal_size().columns


# ── Dragon Ball Banner ────────────────────────────────────────────────

def render_banner(session) -> "Panel":
    from rich.panel import Panel
    from rich.text import Text

    mode_str = session.mode.value.upper()
    policy_str = session.policy.value.upper()
    from deepforge.config import config
    backend_str = config.backend.value.upper()

    tw = _term_width()
    w = max(40, min(92, tw - 4))
    inner_w = w - 2
    h_double = "═" * inner_w
    h_single = "─" * inner_w

    text = Text()

    # ── Top border with dragon ball stars ──
    stars = "★ ★ ★ ★ ★ ★ ★"
    if inner_w < len(stars):
        stars = "★ ★ ★ ★ ★ ★ ★"
    pad_total = inner_w - len(stars)
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    text.append(f"╔{h_double}╗\n", style="bold #FFD700")
    text.append("║", style="bold #FFD700")
    text.append(" " * pad_l, style="")
    text.append(stars, style="bold #FFD700")
    text.append(" " * pad_r, style="")
    text.append("║\n", style="bold #FFD700")

    # ── Dragon ASCII art ──
    dragon_line = "        🐉  D R A G O N   B A L L  🐉"
    if inner_w < len(dragon_line):
        dragon_line = "   🐉  D R A G O N   B A L L  🐉"
    pad_total = inner_w - len(dragon_line)
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    text.append("║", style="bold #FFD700")
    text.append(" " * pad_l, style="")
    text.append(dragon_line, style="bold #2E7D32")
    text.append(" " * pad_r, style="")
    text.append("║\n", style="bold #FFD700")

    # ── Title: DeepForge ──
    title = "D E E P F O R G E"
    if inner_w < len(title):
        title = "DEEPFORGE"
    pad_total = inner_w - len(title)
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    text.append("║", style="bold #FFD700")
    text.append(" " * pad_l, style="")
    text.append(title, style="bold #FF6B35")
    text.append(" " * pad_r, style="")
    text.append("║\n", style="bold #FFD700")

    # ── Subtitle ──
    sub = "⚡ CodeWhale Architecture · Super Saiyan Edition ⚡"
    if inner_w < len(sub):
        sub = "⚡ Super Saiyan Edition ⚡"
    pad_total = inner_w - len(sub)
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    text.append("║", style="bold #FFD700")
    text.append(" " * pad_l, style="")
    text.append(sub, style="#FFD700")
    text.append(" " * pad_r, style="")
    text.append("║\n", style="bold #FFD700")

    # ── Separator ──
    text.append(f"╠{h_double}╣\n", style="bold #FFD700")

    # ── Mode / Policy line ──
    mode_icon = {"agent": "🟠", "plan": "🔵", "yolo": "💀"}.get(session.mode.value, "⚪")
    policy_icon = {"auto": "🟢", "suggest": "⭐", "never": "❌"}.get(session.policy.value, "⚪")

    backend_icon = {"deepseek": "🐋", "azure": "☁️"}.get(config.backend.value, "⚪")
    line1 = f"  {mode_icon} Mode: {mode_str:<6}  {policy_icon} Policy: {policy_str:<6}  {backend_icon} {backend_str}"
    line1 = line1[:inner_w].ljust(inner_w)
    text.append("║", style="bold #FFD700")
    text.append(line1[:20], style="bold #FF6B35")
    text.append(line1[20:], style="bold #FFD700")
    text.append("║\n", style="bold #FFD700")

    # ── Tools / Workspace line ──
    workspace_raw = str(session.workspace)
    ws_max = inner_w - 30
    if len(workspace_raw) > ws_max and ws_max > 10:
        workspace_str = "..." + workspace_raw[-(ws_max - 3):]
    else:
        workspace_str = workspace_raw

    line2 = f"  🛠 Tools: {len(session.available_tools)}  📁 {workspace_str}"
    line2 = line2[:inner_w].ljust(inner_w)
    text.append("║", style="bold #FFD700")
    text.append(line2, style="#00BCD4")
    text.append("║\n", style="bold #FFD700")

    # ── Context window / Reasoning effort (Azure-specific) ──
    if config.backend == Backend.AZURE:
        ctx_tokens = config.azure_context_tokens
        ctx_k = f"{ctx_tokens // 1024}K"
        effort = getattr(session.agent.client, "reasoning_effort", None) if session.agent and session.agent.client else None
        parts = f"  ⚡ Context: {ctx_k}"
        if effort:
            parts += f"  🔥 Eff: {effort}"
        line3 = f"  {parts}"
        line3 = line3[:inner_w].ljust(inner_w)
        text.append("║", style="bold #FFD700")
        text.append(line3, style="#FF6B35")
        text.append("║\n", style="bold #FFD700")

    # ── Commands line ──
    if inner_w >= 50:
        cmds = "/mode /policy /tools /mcp /stats /compact /help /exit /theme"
    else:
        cmds = "/mode /policy /tools /mcp /help /exit"
    cmd_line = f"  💫 {cmds}"
    cmd_line = cmd_line[:inner_w].ljust(inner_w)
    text.append("║", style="bold #FFD700")
    text.append(cmd_line, style="dim #FFD700")
    text.append("║\n", style="bold #FFD700")

    # ── Bottom border with stars ──
    text.append(f"╚{h_double}╝", style="bold #FFD700")

    return Panel(text, border_style="#FFD700")


# ── Status Bar ────────────────────────────────────────────────────────

def render_status_bar(session) -> "Text":
    from rich.text import Text

    ctx_stats = session.get_context_stats()
    pressure = ctx_stats.get("pressure", "low")
    ratio_str = ctx_stats.get("usage_ratio", "0%")
    ratio = float(ratio_str.rstrip("%")) / 100 if ratio_str else 0.0

    # Character icons based on mode
    mode_icons = {
        Mode.AGENT: "🟠",   # Goku
        Mode.PLAN: "🔵",    # Vegeta
        Mode.YOLO: "💀",    # Buu
    }
    icon = mode_icons.get(session.mode, "⚪")

    from deepforge.config import Backend, config

    bar = Text()
    bar.append(f" {icon} ", style="bold #FFD700")
    bar.append(f" {session.mode.value.upper()} ", style=f"{_mode_color(session.mode)} on #1a0a00")
    bar.append(" ")
    bar.append(f" {session.policy.value.upper()} ", style=f"{_policy_color(session.policy)} on #1a0a00")
    bar.append(" ")
    bar.append(f" {config.backend.value.upper()} ", style=f"bold #FFD700 on #1a0a00")
    # Show reasoning effort if set
    effort = getattr(session.agent.client, "reasoning_effort", None) if session.agent and session.agent.client else None
    if effort:
        bar.append(f" 🔥{effort} ", style="bold #FF6B35 on #1a0a00")
    bar.append("  ⚡ Context: ", style="dim #FFD700")
    bar.append_text(_pressure_bar(pressure, ratio))
    bar.append(" ")
    bar.append(f"  🛠 {len(session.available_tools)}", style="dim #00BCD4")
    return bar


# ── Dashboard (shown on /theme dragonball) ────────────────────────────

DRAGON_BALL_CHARACTERS = {
    "Saiyans": [
        ("🟠", "Goku", "Kakarot", "SSJ3 / UI", "∞"),
        ("🔵", "Vegeta", "Prince", "SSJBE", "9.8B"),
        ("🟡", "Gohan", "Son Gohan", "Beast", "9.5B"),
        ("🟣", "Trunks", "Future", "SSJ Rage", "8.0B"),
        ("🔴", "Goten", "Son Goten", "SSJ", "6.0B"),
    ],
    "Villains": [
        ("💀", "Frieza", "Emperor", "Black Frieza", "∞"),
        ("👾", "Cell", "Perfect", "Super Perfect", "8.5B"),
        ("💗", "Buu", "Majin", "Kid Buu", "∞"),
        ("👑", "Beerus", "God", "Ultra Instinct", "∞"),
        ("🌌", "Jiren", "Pride Trooper", "Full Power", "9.9B"),
    ],
    "Allies": [
        ("🐱", "Whis", "Angel", "Ultra Instinct", "∞"),
        ("🐉", "Shenron", "Dragon", "Wish Granting", "∞"),
        ("💚", "Piccolo", "Namekian", "Orange", "7.0B"),
        ("📡", "Bulma", "Genius", "Scientist", "∞ IQ"),
        ("🌍", "Korin", "Guardian", "Senzu Beans", "∞"),
    ],
    "Transformations": [
        ("⭐", "SSJ", "×50 boost", "Golden hair", "First"),
        ("⚡", "SSJ2", "×100 boost", "Lightning", "Cell Saga"),
        ("🔥", "SSJ3", "×400 boost", "No eyebrows", "Buu Saga"),
        ("💫", "SSG", "God ki", "Red hair", "BoG Saga"),
        ("✨", "SSJB", "×SSG×SSJ", "Blue hair", "RoF Saga"),
        ("🌀", "UI", "Autonomous", "Silver hair", "ToP Saga"),
    ],
}

DRAGON_BALLS = [
    ("★", "1-Star", "Orange", "Earth"),
    ("★★", "2-Star", "Orange", "Earth"),
    ("★★★", "3-Star", "Orange", "Earth"),
    ("★★★★", "4-Star", "Orange", "Earth"),
    ("★★★★★", "5-Star", "Orange", "Earth"),
    ("★★★★★★", "6-Star", "Orange", "Earth"),
    ("★★★★★★★", "7-Star", "Orange", "Earth"),
]


def render_dashboard(_session) -> list:
    """Render Dragon Ball character database and dragon balls."""
    from rich.align import Align
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    tw = _term_width()
    outputs = []

    # ── Header ──
    outputs.append(Panel(
        Align.center("[bold #FFD700]🐉 DRAGON BALL DATABASE 🐉[/]"),
        border_style="#FF6B35",
    ))

    # ── Character Tables ──
    # Decide layout based on width
    if tw >= 140:
        per_row = 3  # 3 categories side by side
    elif tw >= 100:
        per_row = 2
    else:
        per_row = 1

    char_panels = []
    for category, chars in DRAGON_BALL_CHARACTERS.items():
        table = Table(box=box.SIMPLE_HEAVY, show_header=True,
                      header_style="bold #FFD700 on #1A237E")
        table.add_column(category, style="bold white", width=14)
        table.add_column("Name", style="bold", width=12)
        table.add_column("Title", style="dim", width=14)
        table.add_column("Form", style="#FFD700", width=14)
        table.add_column("Power", justify="right", style="bold #FF6B35", width=8)

        for icon, name, title, form, power in chars:
            table.add_row(f"{icon} {name}", name, title, form, power)

        char_panels.append(Panel(table, border_style="#1A237E", box=box.ROUNDED, padding=(0, 1)))

    # Arrange in rows
    for i in range(0, len(char_panels), per_row):
        row = char_panels[i:i + per_row]
        outputs.append(Columns(row, equal=True, expand=True))

    # ── Dragon Balls ──
    if tw >= 70:
        db_table = Table(box=box.SIMPLE_HEAVY, show_header=True,
                         header_style="bold #FFD700 on #FF6B35")
        db_table.add_column("", style="bold #FFD700", width=10)
        db_table.add_column("Dragon Ball", style="bold white", width=14)
        db_table.add_column("Color", style="#FF6B35", width=10)
        db_table.add_column("Location", style="dim", width=12)

        for stars, name, color, location in DRAGON_BALLS:
            db_table.add_row(stars, name, color, location)

        outputs.append(Panel(
            db_table,
            title="[bold #FFD700]★ Dragon Balls ★[/]",
            border_style="#FFD700",
            box=box.ROUNDED,
        ))

    # ── Power Level Meter ──
    power_meter = Text()
    power_meter.append("\n")
    power_meter.append("  ⚡ POWER LEVEL: ", style="bold #FF6B35")
    # Draw a power bar
    bar_chars = "▓" * 20
    power_meter.append(bar_chars, style="bold #FFD700")
    power_meter.append(" OVER 9000!!!", style="bold #FF6B35")
    power_meter.append("\n")

    outputs.append(Panel(
        Align.center(power_meter),
        border_style="#FF6B35",
    ))

    return outputs


# ── CodeWhale Layout Colors ──────────────────────────────────────────

CODEWHALE_COLORS = {
    "bg": "default",
    "surface": "default",
    "surface_hi": "default",
    "border": "#FF6B35",
    "border_dim": "#8B4513",
    "blue": "#FFD700",
    "cyan": "#FFD700",
    "green": "#2E7D32",
    "yellow": "#FFD700",
    "orange": "#FF6B35",
    "muted": "#A08060",
    "text": "#FFE4C4",
    "dim_text": "#8B7355",
    "error": "#FF4081",
    "icon": "🐉",
    "progress": "#FFD700",
    "complete": "#FFD700",
    "running": "#FF6B35",
    "failed": "#FF4081",
}


# ── Register ──────────────────────────────────────────────────────────

theme = Theme(
    name="dragonball",
    label="🐉 Dragon Ball — Goku orange, Vegeta blue, Shenron green, Super Saiyan gold",
    colors=COLORS,
    codewhale_colors=CODEWHALE_COLORS,
    render_banner=render_banner,
    render_status_bar=render_status_bar,
    render_dashboard=render_dashboard,
)
register(theme)
