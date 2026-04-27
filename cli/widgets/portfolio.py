"""Portfolio display widgets for the CLI."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shared.types import PortfolioState, RiskSnapshot

console = Console()


def render_portfolio(portfolio: PortfolioState) -> Panel:
    """Render the portfolio state as a Rich panel."""
    table = Table(
        show_header=True,
        header_style="bold white",
        border_style="blue",
        expand=True,
    )
    table.add_column("Ticker", style="bold")
    table.add_column("Side", justify="center")
    table.add_column("Qty", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Kelly %", justify="right")

    for pos in portfolio.positions:
        pnl = pos.unrealized_pnl
        pnl_style = "green" if pnl >= 0 else "red"
        side_style = "bold green" if pos.side.value == "LONG" else "bold red"

        table.add_row(
            pos.ticker,
            Text(pos.side.value, style=side_style),
            str(pos.quantity),
            f"${pos.avg_entry_price:,.2f}",
            f"${pos.current_price:,.2f}",
            Text(f"${pnl:+,.2f}", style=pnl_style),
            f"{pos.kelly_allocation:.2%}",
        )

    summary = (
        f"Cash: ${portfolio.cash:,.2f}  |  "
        f"Equity: ${portfolio.total_equity:,.2f}  |  "
        f"P&L: ${portfolio.total_pnl:+,.2f}  |  "
        f"DD: {portfolio.drawdown_pct:.2f}%"
    )

    return Panel(
        table,
        title="[bold blue]Portfolio[/bold blue]",
        subtitle=summary,
        border_style="blue",
    )


def render_risk(risk: RiskSnapshot) -> Panel:
    """Render risk metrics as a Rich panel."""
    dd_style = (
        "green"
        if risk.current_drawdown_pct < 1.0
        else ("yellow" if risk.current_drawdown_pct < 2.0 else "bold red")
    )

    content = (
        f"  VaR (95%):    {risk.value_at_risk_95:.4f}\n"
        f"  VaR (99%):    {risk.value_at_risk_99:.4f}\n"
        f"  Drawdown:     [{dd_style}]{risk.current_drawdown_pct:.2f}%[/{dd_style}]\n"
        f"  Max Drawdown: {risk.max_drawdown_pct:.2f}%\n"
        f"  Volatility:   {risk.portfolio_volatility:.4f}\n"
        f"  Kill Switch:  {'[green]ARMED[/green]' if risk.kill_switch_armed else '[red]DISARMED[/red]'}"
    )

    return Panel(
        content,
        title="[bold yellow]Risk Metrics[/bold yellow]",
        border_style="yellow",
    )
