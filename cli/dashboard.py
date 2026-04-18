"""Live monitoring dashboard — the crown jewel of the AYVID CLI.

Provides a real-time terminal dashboard showing:
- Portfolio positions and P&L
- Active trade signals
- Risk metrics and kill switch status
- System health
"""
from __future__ import annotations

import asyncio
import datetime as dt

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shared.config import settings

console = Console()

_REFRESH_SECONDS = 5


def _build_header() -> Panel:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "[green]PAPER[/green]" if settings.PAPER_TRADING else "[bold red]LIVE[/bold red]"
    llm = settings.LLM_PROVIDER.upper()

    return Panel(
        f"  [bold cyan]AYVID[/bold cyan] — Asymmetric Yield Via Intelligent Decisions    "
        f"Mode: {mode}  |  LLM: {llm}  |  {now}",
        border_style="cyan",
        height=3,
    )


def _build_portfolio_panel() -> Panel:
    from backend.services.orchestrator import get_portfolio_state

    state = get_portfolio_state()

    table = Table(show_header=True, header_style="bold", border_style="blue", expand=True)
    table.add_column("Ticker", style="bold")
    table.add_column("Side", justify="center")
    table.add_column("Qty", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Kelly", justify="right")

    for pos in state.positions:
        pnl_style = "green" if pos.unrealized_pnl >= 0 else "red"
        side_style = "green" if pos.side.value == "LONG" else "red"
        table.add_row(
            pos.ticker,
            Text(pos.side.value, style=side_style),
            str(pos.quantity),
            f"${pos.avg_entry_price:,.2f}",
            Text(f"${pos.unrealized_pnl:+,.2f}", style=pnl_style),
            f"{pos.kelly_allocation:.2%}",
        )

    if not state.positions:
        table.add_row("[dim]No positions[/dim]", "", "", "", "", "")

    subtitle = (
        f"Cash: ${state.cash:,.0f} | Equity: ${state.total_equity:,.0f} | "
        f"P&L: ${state.total_pnl:+,.0f} | DD: {state.drawdown_pct:.1f}%"
    )

    return Panel(table, title="[bold blue]Portfolio[/bold blue]", subtitle=subtitle, border_style="blue")


def _build_signals_panel() -> Panel:
    from backend.services.orchestrator import get_recent_signals

    sigs = get_recent_signals(10)

    table = Table(show_header=True, header_style="bold", border_style="magenta", expand=True)
    table.add_column("ID", style="dim", width=8)
    table.add_column("Ticker", style="bold")
    table.add_column("Action", justify="center")
    table.add_column("Conf", justify="right")
    table.add_column("Horizon")
    table.add_column("Status")

    for sig in sigs[-8:]:
        action_style = "green" if sig.action.value == "LONG" else "red"
        status_style = {"FILLED": "green", "PENDING": "yellow", "REJECTED": "red"}.get(
            sig.status.value, "dim"
        )
        table.add_row(
            sig.signal_id[:8],
            sig.ticker,
            Text(sig.action.value, style=action_style),
            f"{sig.confidence:.0%}",
            sig.alpha_horizon.value,
            Text(sig.status.value, style=status_style),
        )

    if not sigs:
        table.add_row("[dim]No signals[/dim]", "", "", "", "", "")

    return Panel(table, title="[bold magenta]Signals[/bold magenta]", border_style="magenta")


def _build_risk_panel() -> Panel:
    from backend.services.orchestrator import get_risk_snapshot

    risk = get_risk_snapshot()
    dd_style = "green" if risk.current_drawdown_pct < 1 else (
        "yellow" if risk.current_drawdown_pct < 2 else "bold red"
    )

    content = (
        f"  VaR 95%:   {risk.value_at_risk_95:.4f}\n"
        f"  VaR 99%:   {risk.value_at_risk_99:.4f}\n"
        f"  Drawdown:  [{dd_style}]{risk.current_drawdown_pct:.2f}%[/{dd_style}]\n"
        f"  Max DD:    {risk.max_drawdown_pct:.2f}%\n"
        f"  Kill:      {'[green]ARMED[/green]' if risk.kill_switch_armed else '[red]OFF[/red]'}"
    )

    return Panel(content, title="[bold yellow]Risk[/bold yellow]", border_style="yellow")


def _build_system_panel() -> Panel:
    from execution_engine.broker.orders import is_regular_trading_hours, get_queued_orders

    rth = is_regular_trading_hours()
    queued = len(get_queued_orders())

    content = (
        f"  LLM:      {settings.LLM_PROVIDER.upper()} ({settings.OPENAI_MODEL})\n"
        f"  Trading:  {'[green]RTH[/green]' if rth else '[yellow]CLOSED[/yellow]'}\n"
        f"  Queued:   {queued}\n"
        f"  Paper:    {'[green]YES[/green]' if settings.PAPER_TRADING else '[red]LIVE[/red]'}"
    )

    return Panel(content, title="[bold]System[/bold]", border_style="dim")


def _build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=8),
    )
    layout["body"].split_row(
        Layout(name="portfolio", ratio=3),
        Layout(name="signals", ratio=2),
    )
    layout["footer"].split_row(
        Layout(name="risk", ratio=1),
        Layout(name="system", ratio=1),
    )

    layout["header"].update(_build_header())
    layout["portfolio"].update(_build_portfolio_panel())
    layout["signals"].update(_build_signals_panel())
    layout["risk"].update(_build_risk_panel())
    layout["system"].update(_build_system_panel())

    return layout


def run_dashboard() -> None:
    """Run the live dashboard with auto-refresh."""
    console.print("\n[bold cyan]Starting AYVID Dashboard...[/bold cyan]")
    console.print("[dim]Press Ctrl+C to exit[/dim]\n")

    try:
        with Live(
            _build_layout(),
            console=console,
            refresh_per_second=1,
            screen=True,
        ) as live:
            while True:
                live.update(_build_layout())
                import time
                time.sleep(_REFRESH_SECONDS)
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard stopped.[/dim]")
