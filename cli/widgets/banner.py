"""AYVID banner and branding for the CLI."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

_BANNER = """
   █████╗ ██╗   ██╗██╗   ██╗██╗██████╗ 
  ██╔══██╗╚██╗ ██╔╝██║   ██║██║██╔══██╗
  ███████║ ╚████╔╝ ██║   ██║██║██║  ██║
  ██╔══██║  ╚██╔╝  ╚██╗ ██╔╝██║██║  ██║
  ██║  ██║   ██║    ╚████╔╝ ██║██████╔╝
  ╚═╝  ╚═╝   ╚═╝     ╚═══╝  ╚═╝╚═════╝ 
"""


def print_banner() -> None:
    banner_text = Text(_BANNER, style="bold cyan")
    subtitle = Text(
        "  Asymmetric Yield Via Intelligent Decisions\n"
        "  AI-Native Quantitative Hedge Fund\n"
        "  N-th Order Causal Arbitrage | LEOATIS Labs | YC S26\n",
        style="dim white",
    )

    console.print(Panel(
        banner_text + subtitle,
        border_style="cyan",
        padding=(0, 2),
    ))

    console.print(
        "\n  [dim]Command Groups:[/dim]\n"
        "    [cyan]ayvid data[/cyan]       Market data, backtesting, universe scanning (19 commands)\n"
        "    [cyan]ayvid graph[/cyan]      Supply chain graph intelligence (6 commands)\n"
        "    [cyan]ayvid trade[/cyan]      Portfolio, signals, execution, Kelly sizing (6 commands)\n"
        "    [cyan]ayvid system[/cyan]     Health checks, config, API server (4 commands)\n"
        "    [cyan]ayvid dashboard[/cyan]  Live Rich terminal monitoring\n"
        "\n  [dim]Quick Start:[/dim]\n"
        "    [cyan]ayvid data backtest[/cyan]         Run two-track backtest (Index F&O + Equity)\n"
        "    [cyan]ayvid data backtest --fresh[/cyan]  Refresh data + dynamic screening first\n"
        "    [cyan]ayvid graph analyze TSM[/cyan]     Causal arbitrage analysis on a shock\n"
        "    [cyan]ayvid system status[/cyan]         Check all service connections\n"
    )
