"""Trade signal display widgets for the CLI."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shared.types import TradeSignal

console = Console()


def render_signals(signals: list[TradeSignal], title: str = "Trade Signals") -> Panel:
    """Render trade signals as a Rich panel."""
    table = Table(
        show_header=True,
        header_style="bold white",
        border_style="magenta",
        expand=True,
    )
    table.add_column("ID", style="dim")
    table.add_column("Time", style="dim")
    table.add_column("Ticker", style="bold")
    table.add_column("Action", justify="center")
    table.add_column("Horizon", justify="center")
    table.add_column("Conf.", justify="right")
    table.add_column("Risk", justify="right")
    table.add_column("Status", justify="center")

    for sig in signals[-15:]:
        action_style = (
            "bold green"
            if sig.action.value == "LONG"
            else ("bold red" if sig.action.value == "SHORT" else "dim")
        )
        status_style = {
            "PENDING": "yellow",
            "QUEUED": "cyan",
            "EXECUTING": "blue",
            "FILLED": "green",
            "CANCELLED": "dim",
            "REJECTED": "red",
        }.get(sig.status.value, "white")

        table.add_row(
            sig.signal_id[:8],
            sig.timestamp.strftime("%H:%M:%S"),
            sig.ticker,
            Text(sig.action.value, style=action_style),
            sig.alpha_horizon.value,
            f"{sig.confidence:.0%}",
            f"{sig.compound_risk:.3f}" if sig.compound_risk else "-",
            Text(sig.status.value, style=status_style),
        )

    return Panel(
        table,
        title=f"[bold magenta]{title}[/bold magenta]",
        border_style="magenta",
    )


def render_signal_detail(signal: TradeSignal) -> Panel:
    """Render a detailed view of a single trade signal."""
    action_style = "green" if signal.action.value == "LONG" else "red"

    path_str = " -> ".join(signal.causal_path) if signal.causal_path else "N/A"

    content = (
        f"  Signal ID:     {signal.signal_id}\n"
        f"  Ticker:        [bold]{signal.ticker}[/bold]\n"
        f"  Action:        [{action_style}]{signal.action.value}[/{action_style}]\n"
        f"  Horizon:       {signal.alpha_horizon.value}\n"
        f"  Confidence:    {signal.confidence:.0%}\n"
        f"  Compound Risk: {signal.compound_risk or 0:.4f}\n"
        f"  Causal Path:   {path_str}\n"
        f"  Status:        {signal.status.value}\n"
        f"\n  [bold]Rationale:[/bold]\n  {signal.rationale}"
    )

    return Panel(
        content,
        title=f"[bold]Signal: {signal.action.value} {signal.ticker}[/bold]",
        border_style=action_style,
    )
