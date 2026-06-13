"""
Demo: CodeWhale-style adaptive layout with Rich.
Run: python layout_demo.py
Resize your terminal to see it adapt in real time.
"""

import time
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

console = Console()

def build_layout(session_info: dict) -> Layout:
    """声明式构建布局 — Rich 自动处理边框和尺寸"""
    root = Layout()

    # 分成三行：顶部 6 行 / 主区域 flex / 底部 3 行
    root.split_column(
        Layout(name="header", size=6),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3),
    )

    # ── 顶部：CodeWhale 风格状态面板 ──
    tw = console.width
    header = Panel(
        Text.from_markup(
            f"[bold cyan]Mode:[/] {session_info['mode']}   "
            f"[bold yellow]Policy:[/] {session_info['policy']}   "
            f"[dim]Tools: {session_info['tools']}   "
            f"Workspace: {session_info['workspace'][:tw-50]}"
        ),
        title="DeepForge",
        border_style="cyan",
    )
    root["header"].update(header)

    # ── 主区域：自动填满 ──
    body_text = Text()
    body_text.append("Agent response appears here.\n", style="white")
    body_text.append("Rich Layout handles all borders automatically.\n", style="dim")
    body_text.append(f"Terminal: {tw}x{console.height}\n", style="dim cyan")
    root["body"].update(Panel(body_text, title="Output", border_style="green"))

    # ── 底部：输入提示 ──
    root["footer"].update(Panel(
        "[bold cyan]deepforge[/]› _",
        border_style="dim",
    ))

    return root


def main():
    info = {
        "mode": "AGENT",
        "policy": "SUGGEST",
        "tools": 12,
        "workspace": "/Users/zhaoyu/code_repos/deepforge",
    }

    with Live(build_layout(info), console=console, auto_refresh=False) as live:
        for _ in range(100):
            time.sleep(0.5)
            live.update(build_layout(info))


if __name__ == "__main__":
    main()
