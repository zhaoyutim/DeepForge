"""
World Cup 2026 theme — football pitch green, gold trophy, knockout bracket.

Usage:
    /theme worldcup      — activate and show dashboard
    /theme worldcup off  — switch back to default
    python tui.py --theme worldcup
"""

from themes import Theme, register
from deepforge.config import ApprovalPolicy, Backend, Mode

# ── Colors ────────────────────────────────────────────────────────────

COLORS = {
    "pitch_green": "#1E8E3E",
    "gold": "#F9AB00",
    "dark_blue": "#1A237E",
    "red": "#EA4335",
    "white": "#FFFFFF",
    "banner": "bold yellow",
    "banner_border": "bold green",
    "banner_text": "bold yellow",
    "group_header": "bold yellow on #1E8E3E",
    "knockout_line": "bright_green",
    "champion": "bold gold1",
    # Forward-compat keys (used by TUI helpers)
    "mode_agent": "bold green",
    "mode_plan": "bold yellow",
    "mode_yolo": "bold red",
    "policy_auto": "green",
    "policy_suggest": "yellow",
    "policy_never": "red",
    "context_low": "green",
    "context_medium": "yellow",
    "context_high": "orange1",
    "context_critical": "red",
}

# ── Helpers ───────────────────────────────────────────────────────────

def _mode_color(mode: Mode) -> str:
    return {
        Mode.AGENT: "bold green",
        Mode.PLAN: "bold yellow",
        Mode.YOLO: "bold red",
    }.get(mode, "white")


def _policy_color(policy: ApprovalPolicy) -> str:
    return {
        ApprovalPolicy.AUTO: "green",
        ApprovalPolicy.SUGGEST: "yellow",
        ApprovalPolicy.NEVER: "red",
    }.get(policy, "white")


def _pressure_color(pressure: str) -> str:
    return {
        "low": "green",
        "medium": "yellow",
        "high": "orange1",
        "critical": "red",
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


# ── World Cup Data ────────────────────────────────────────────────────

WORLDCUP_GROUPS = {
    "A": [
        ("🇺🇸", "USA", 3, 3, 0, 0, 7, 1, 9),
        ("🇲🇽", "MEX", 3, 1, 1, 1, 4, 3, 4),
        ("🇳🇱", "NED", 3, 1, 0, 2, 3, 5, 3),
        ("🇯🇵", "JPN", 3, 0, 1, 2, 2, 7, 1),
    ],
    "B": [
        ("🇫🇷", "FRA", 3, 2, 1, 0, 8, 2, 7),
        ("🇩🇪", "GER", 3, 1, 2, 0, 5, 3, 5),
        ("🇰🇷", "KOR", 3, 1, 0, 2, 3, 6, 3),
        ("🇲🇦", "MAR", 3, 0, 1, 2, 2, 7, 1),
    ],
    "C": [
        ("🇧🇷", "BRA", 3, 3, 0, 0, 9, 0, 9),
        ("🇵🇹", "POR", 3, 2, 0, 1, 6, 3, 6),
        ("🇺🇾", "URU", 3, 1, 0, 2, 3, 5, 3),
        ("🇸🇳", "SEN", 3, 0, 0, 3, 1, 9, 0),
    ],
    "D": [
        ("🇦🇷", "ARG", 3, 2, 1, 0, 7, 2, 7),
        ("🇪🇸", "ESP", 3, 1, 2, 0, 5, 3, 5),
        ("🇭🇷", "CRO", 3, 1, 1, 1, 4, 4, 4),
        ("🇧🇪", "BEL", 3, 0, 0, 3, 1, 8, 1),
    ],
    "E": [
        ("🏴󠁧󠁢󠁥󠁮󠁧󠁿", "ENG", 3, 2, 1, 0, 6, 1, 7),
        ("🇩🇰", "DEN", 3, 1, 1, 1, 3, 3, 4),
        ("🇨🇴", "COL", 3, 1, 0, 2, 2, 6, 3),
        ("🇦🇺", "AUS", 3, 0, 2, 1, 2, 3, 2),
    ],
    "F": [
        ("🇮🇹", "ITA", 3, 2, 0, 1, 5, 2, 6),
        ("🇨🇮", "CIV", 3, 1, 2, 0, 4, 3, 5),
        ("🇵🇾", "PAR", 3, 1, 0, 2, 3, 5, 3),
        ("🇶🇦", "QAT", 3, 0, 2, 1, 2, 4, 2),
    ],
    "G": [
        ("🇨🇦", "CAN", 3, 2, 1, 0, 5, 2, 7),
        ("🇨🇭", "SUI", 3, 1, 1, 1, 3, 2, 4),
        ("🇳🇬", "NGA", 3, 1, 0, 2, 4, 6, 3),
        ("🇸🇦", "KSA", 3, 0, 2, 1, 2, 5, 2),
    ],
    "H": [
        ("🇨🇱", "CHI", 3, 2, 0, 1, 6, 3, 6),
        ("🇸🇪", "SWE", 3, 1, 2, 0, 4, 2, 5),
        ("🇪🇨", "ECU", 3, 1, 1, 1, 3, 4, 4),
        ("🇬🇭", "GHA", 3, 0, 0, 3, 1, 7, 0),
    ],
}

KNOCKOUT_MATCHES = {
    "R16_1": ("🇺🇸 USA", "🇫🇷 FRA", 1),
    "R16_2": ("🇧🇷 BRA", "🇲🇽 MEX", 0),
    "R16_3": ("🇦🇷 ARG", "🇭🇷 CRO", 0),
    "R16_4": ("🇪🇸 ESP", "🇧🇪 BEL", 0),
    "R16_5": ("🏴󠁧󠁢󠁥󠁮󠁧󠁿 ENG", "🇩🇰 DEN", 0),
    "R16_6": ("🇮🇹 ITA", "🇨🇮 CIV", 0),
    "R16_7": ("🇨🇦 CAN", "🇨🇭 SUI", 0),
    "R16_8": ("🇨🇱 CHI", "🇸🇪 SWE", 0),
    "QF_1": ("🇫🇷 FRA", "🇧🇷 BRA", 1),
    "QF_2": ("🇦🇷 ARG", "🇪🇸 ESP", 0),
    "QF_3": ("🏴󠁧󠁢󠁥󠁮󠁧󠁿 ENG", "🇮🇹 ITA", 0),
    "QF_4": ("🇨🇦 CAN", "🇨🇱 CHI", 0),
    "SF_1": ("🇧🇷 BRA", "🇦🇷 ARG", 1),
    "SF_2": ("🏴󠁧󠁢󠁥󠁮󠁧󠁿 ENG", "🇨🇦 CAN", 0),
    "FINAL": ("🇦🇷 ARG", "🏴󠁧󠁢󠁥󠁮󠁧󠁿 ENG", 0),
    "THIRD": ("🇧🇷 BRA", "🇨🇦 CAN", 0),
}


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

    # Responsive width
    tw = _term_width()
    w = max(40, min(92, tw - 4))
    inner_w = w - 2
    h_double = "═" * inner_w
    h_single = "─" * inner_w

    text = Text()

    # Top border
    text.append(f"╔{h_double}╗\n", style="bold green")

    # Title — centered
    title = "⚽  CODE X  WORLD CUP EDITION  🏆"
    if inner_w < len(title):
        title = "⚽ CODE X WORLD CUP 🏆"
    pad_total = inner_w - len(title)
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    text.append("║", style="bold green")
    text.append(" " * pad_l, style="")
    text.append(title, style="bold yellow")
    text.append(" " * pad_r, style="")
    text.append("║\n", style="bold green")

    # Subtitle
    sub = "🇺🇸 🇨🇦 🇲🇽  FIFA World Cup 2026"
    if inner_w < len(sub):
        sub = "FIFA World Cup 2026"
    pad_total = inner_w - len(sub)
    pad_l = pad_total // 2
    pad_r = pad_total - pad_l
    text.append("║", style="bold green")
    text.append(" " * pad_l, style="")
    text.append(sub, style="bold white")
    text.append(" " * pad_r, style="")
    text.append("║\n", style="bold green")

    # Separator
    text.append(f"╠{h_double}╣\n", style="bold green")

    # Mode / Policy / Backend
    line1 = f"  Mode: {mode_str:<6}  Policy: {policy_str:<6}  Backend: {backend_str}"
    line1 = line1[:inner_w].ljust(inner_w)
    text.append("║", style="bold green")
    text.append(line1[:18], style="bold yellow")
    text.append(line1[18:], style="bold yellow")
    text.append("║\n", style="bold green")

    # Tools / Commands
    line2 = f"  Tools: {len(session.available_tools)}  /mcp /theme /help /exit"
    line2 = line2[:inner_w].ljust(inner_w)
    text.append("║", style="bold green")
    text.append(line2, style="dim")
    text.append("║\n", style="bold green")

    # ── Context window / Reasoning effort (Azure-specific) ──
    if config.backend == Backend.AZURE:
        ctx_tokens = config.azure_context_tokens
        ctx_k = f"{ctx_tokens // 1024}K"
        effort = getattr(session.agent.client, "reasoning_effort", None) if session.agent and session.agent.client else None
        parts = f"  ⚡ Context: {ctx_k}"
        if effort:
            parts += f"  🏆 Eff: {effort}"
        line3 = f"  {parts}"
        line3 = line3[:inner_w].ljust(inner_w)
        text.append("║", style="bold green")
        text.append(line3, style="bold yellow")
        text.append("║\n", style="bold green")

    # Bottom
    text.append(f"╚{h_double}╝", style="bold green")

    return Panel(text, border_style="green")


# ── Status Bar ────────────────────────────────────────────────────────

def render_status_bar(session) -> "Text":
    from rich.text import Text

    ctx_stats = session.get_context_stats()
    pressure = ctx_stats.get("pressure", "low")
    ratio_str = ctx_stats.get("usage_ratio", "0%")
    ratio = float(ratio_str.rstrip("%")) / 100 if ratio_str else 0.0

    from deepforge.config import Backend, config

    bar = Text()
    bar.append(" ⚽ ", style="bold yellow")
    bar.append(f" {session.mode.value.upper()} ", style="bold green")
    bar.append(f" {session.policy.value.upper()} ", style="bold yellow")
    bar.append(f" {config.backend.value.upper()} ", style="bold green")
    # Show reasoning effort if set
    effort = getattr(session.agent.client, "reasoning_effort", None) if session.agent and session.agent.client else None
    if effort:
        bar.append(f" 🏆{effort} ", style="bold yellow")
    bar.append("  Context: ", style="dim")
    bar.append_text(_pressure_bar(pressure, ratio))
    bar.append(" ")
    bar.append(f"  Tools: {len(session.available_tools)}", style="dim")
    return bar


# ── Dashboard (shown on /theme worldcup) ──────────────────────────────

def render_dashboard(_session) -> list:
    """Render group stage + knockout bracket, adapting to terminal width."""
    from rich.align import Align
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    tw = _term_width()
    outputs = []

    # ── Group stage ────────────────────────────────────────────────
    outputs.append(Panel(
        Align.center("[bold yellow]⚽ FIFA WORLD CUP 2026 — GROUP STAGE ⚽[/]"),
        border_style="green",
    ))

    # Decide how many groups per row based on terminal width
    if tw >= 150:
        per_row = 8   # All 8 groups in one row
    elif tw >= 110:
        per_row = 4   # 2 rows of 4
    elif tw >= 70:
        per_row = 2   # 4 rows of 2
    else:
        per_row = 1   # 8 rows of 1

    # Build group panels with responsive widths
    group_panels = []
    for group_name, teams in WORLDCUP_GROUPS.items():
        table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold yellow on #1E8E3E")
        # Adjust column widths based on per_row
        if per_row >= 4:
            name_w, pts_w, gd_w = 18, 4, 4
        elif per_row >= 2:
            name_w, pts_w, gd_w = 16, 4, 4
        else:
            name_w, pts_w, gd_w = 20, 4, 4

        table.add_column(f"GROUP {group_name}", style="bold white", width=name_w)
        table.add_column("PTS", justify="center", style="bold yellow", width=pts_w)
        table.add_column("GD", justify="center", style="dim", width=gd_w)

        for idx, (flag, name, _mp, _w, _d, _l, gf, ga, pts) in enumerate(teams):
            gd = gf - ga
            gd_str = f"+{gd}" if gd > 0 else str(gd)
            row_style = "bold green" if idx < 2 else ""
            # On narrow terminals, shorten the display
            disp = f"  {flag} {name}"
            if per_row == 1 and len(disp) > 20:
                disp = f"  {flag} {name[:3]}"
            table.add_row(disp, str(pts), gd_str, style=row_style)

        group_panels.append(Panel(table, border_style="green", box=box.ROUNDED, padding=(0, 1)))

    # Arrange panels in rows
    for i in range(0, len(group_panels), per_row):
        row = group_panels[i:i + per_row]
        outputs.append(Columns(row, equal=True, expand=True))

    # ── Knockout bracket ───────────────────────────────────────────
    m = KNOCKOUT_MATCHES
    r16_winners = [m[k][m[k][2]] for k in (
        "R16_1", "R16_2", "R16_3", "R16_4", "R16_5", "R16_6", "R16_7", "R16_8",
    )]
    qf_winners = [m[k][m[k][2]] for k in ("QF_1", "QF_2", "QF_3", "QF_4")]
    sf_winners = [m[k][m[k][2]] for k in ("SF_1", "SF_2")]
    champion = m["FINAL"][m["FINAL"][2]]
    third = m["THIRD"][m["THIRD"][2]]

    # Rich markup helpers
    def w(t): return f"[bold white]{t}[/bold white]"
    def g(t): return f"[bold green]{t}[/bold green]"
    def c(t): return f"[bold gold1]{t} 🏆[/bold gold1]"

    # Choose bracket spacing based on width
    if tw >= 130:
        s = [
            "[dim]  R16          Quarter        Semi          Final[/]",
            "[dim]──────────    ──────────    ──────────    ──────────[/]",
            f"  {w(r16_winners[0])} ─┐",
            f"                ├── {g(qf_winners[0])} ─┐",
            f"  {w(r16_winners[1])} ─┘                │",
            f"                                 ├── {g(sf_winners[0])} ─┐",
            f"  {w(r16_winners[2])} ─┐                │                │",
            f"                ├── {g(qf_winners[1])} ─┘                │",
            f"  {w(r16_winners[3])} ─┘                                 │",
            f"                                                  ├── {c(champion)}",
            f"  {w(r16_winners[4])} ─┐                                 │",
            f"                ├── {g(qf_winners[2])} ─┐                │",
            f"  {w(r16_winners[5])} ─┘                │                │",
            f"                                 ├── {g(sf_winners[1])} ─┘",
            f"  {w(r16_winners[6])} ─┐                │",
            f"                ├── {g(qf_winners[3])} ─┘",
            f"  {w(r16_winners[7])} ─┘",
        ]
    elif tw >= 90:
        s = [
            "[dim]  R16         Quarter      Semi        Final[/]",
            "[dim]─────────    ─────────    ────────    ────────[/]",
            f"  {w(r16_winners[0])} ─┐",
            f"               ├── {g(qf_winners[0])} ─┐",
            f"  {w(r16_winners[1])} ─┘              │",
            f"                               ├── {g(sf_winners[0])} ─┐",
            f"  {w(r16_winners[2])} ─┐              │                │",
            f"               ├── {g(qf_winners[1])} ─┘              │",
            f"  {w(r16_winners[3])} ─┘                               │",
            f"                                                ├── {c(champion)}",
            f"  {w(r16_winners[4])} ─┐                               │",
            f"               ├── {g(qf_winners[2])} ─┐              │",
            f"  {w(r16_winners[5])} ─┘              │                │",
            f"                               ├── {g(sf_winners[1])} ─┘",
            f"  {w(r16_winners[6])} ─┐              │",
            f"               ├── {g(qf_winners[3])} ─┘",
            f"  {w(r16_winners[7])} ─┘",
        ]
    else:
        s = [
            "[dim]  R16          Quarter       Semi[/]",
            "[dim]─────────    ──────────    ────────[/]",
            f"  {w(r16_winners[0])} ─┐",
            f"               ├── {g(qf_winners[0])} ─┐",
            f"  {w(r16_winners[1])} ─┘              │",
            f"                               ├── {g(sf_winners[0])}",
            f"  {w(r16_winners[2])} ─┐              │",
            f"               ├── {g(qf_winners[1])} ─┘",
            f"  {w(r16_winners[3])} ─┘",
            "",
            f"  {w(r16_winners[4])} ─┐",
            f"               ├── {g(qf_winners[2])} ─┐",
            f"  {w(r16_winners[5])} ─┘              │",
            f"                               ├── {g(sf_winners[1])}",
            f"  {w(r16_winners[6])} ─┐              │",
            f"               ├── {g(qf_winners[3])} ─┘",
            f"  {w(r16_winners[7])} ─┘",
            "",
            f"  🏆 CHAMPION: {c(champion)}",
        ]

    # Build bracket using Text.from_markup for each line
    bracket = Text()
    for line in s:
        if line:
            bracket.append(Text.from_markup(line))
        bracket.append("\n")
    bracket.append("\n")
    bracket.append(Text.from_markup(f"  🥉 Third Place: {g(third)}\n"))

    title = "⚡ KNOCKOUT STAGE" if tw >= 90 else "⚡ KNOCKOUT"
    # Use simple border — content is self-styled already
    outputs.append(Panel(bracket, title=title, border_style="green", box=box.SIMPLE))

    return outputs


# ── Register ──────────────────────────────────────────────────────────

theme = Theme(
    name="worldcup",
    label="World Cup 2026 — football pitch green, group tables, knockout bracket",
    colors=COLORS,
    render_banner=render_banner,
    render_status_bar=render_status_bar,
    render_dashboard=render_dashboard,
)
register(theme)
