"""Trading signals & execution CLI commands."""
from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
trade_app = typer.Typer(help="Trading signals & execution", no_args_is_help=True)


@trade_app.command("portfolio")
def portfolio():
    """Display current portfolio positions and P&L."""
    from backend.services.orchestrator import get_portfolio_state
    from cli.widgets.portfolio import render_portfolio

    state = get_portfolio_state()
    console.print(render_portfolio(state))


@trade_app.command("signals")
def signals():
    """Display recent trade signals."""
    from backend.services.orchestrator import get_recent_signals
    from cli.widgets.signals import render_signals

    sigs = get_recent_signals()
    if not sigs:
        console.print("[yellow]No signals generated yet.[/yellow]")
        console.print("[dim]Run 'ayvid graph analyze <TICKER>' to generate signals.[/dim]")
        return
    console.print(render_signals(sigs))


@trade_app.command("execute")
def execute(
    ticker: str = typer.Argument(help="Ticker to trade"),
    action: str = typer.Argument(help="LONG or SHORT"),
    confidence: float = typer.Option(0.7, help="Signal confidence (0-1)"),
):
    """Manually execute a paper trade."""
    action = action.upper()
    if action not in ("LONG", "SHORT"):
        console.print("[red]Action must be LONG or SHORT[/red]")
        raise typer.Exit(1)

    async def _run():
        from backend.services.orchestrator import execute_manual_signal
        console.print(
            f"\n[bold]Executing: {action} {ticker} (confidence={confidence:.0%})[/bold]\n"
        )
        result = await execute_manual_signal(ticker, action, confidence)
        console.print(Panel(
            f"  Status: [bold]{result.get('status', 'unknown')}[/bold]\n"
            f"  Ticker: {result.get('ticker', ticker)}\n"
            f"  Action: {result.get('action', action)}",
            title="[bold]Trade Result[/bold]",
            border_style="green" if result.get("status") == "FILLED" else "red",
        ))

    asyncio.run(_run())


@trade_app.command("risk")
def risk():
    """Display current risk metrics."""
    async def _run():
        from backend.services.orchestrator import refresh_risk
        from cli.widgets.portfolio import render_risk

        snapshot = await refresh_risk()
        console.print(render_risk(snapshot))

    asyncio.run(_run())


@trade_app.command("sizing")
def sizing(
    ticker: str = typer.Argument(help="Ticker to size"),
    confidence: float = typer.Option(0.7, help="Win probability (0-1)"),
    equity: float = typer.Option(1_000_000, help="Portfolio equity"),
):
    """Calculate Kelly Criterion position sizing for a ticker."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_ohlcv
        from data_primitives.loaders.hopsworks_sync import (
            compute_rolling_volatility, compute_expected_returns,
        )
        from execution_engine.math.kelly import optimal_allocation_for_signal

        bars = await fetch_ohlcv(ticker, days=90)
        vol = compute_rolling_volatility(bars)
        ret = compute_expected_returns(bars)
        price = bars[-1].close if bars else 100.0

        frac, shares = optimal_allocation_for_signal(
            portfolio_equity=equity,
            win_probability=confidence,
            expected_return=ret,
            volatility=vol,
            current_price=price,
        )

        console.print(Panel(
            f"  Ticker:          [bold]{ticker}[/bold]\n"
            f"  Current Price:   ${price:,.2f}\n"
            f"  30d Volatility:  {vol:.2%}\n"
            f"  Expected Return: {ret:.2%}\n"
            f"  Win Probability: {confidence:.0%}\n"
            f"  ─────────────────────────────\n"
            f"  Kelly Fraction:  [bold]{frac:.4f}[/bold] (Half-Kelly)\n"
            f"  Share Count:     [bold]{shares:,}[/bold]\n"
            f"  Notional Value:  [bold]${shares * price:,.2f}[/bold]\n"
            f"  % of Portfolio:  {frac:.2%}",
            title="[bold]Kelly Criterion Sizing[/bold]",
            border_style="blue",
        ))

    asyncio.run(_run())


@trade_app.command("queued")
def queued():
    """Show orders queued during pre-market hours."""
    from execution_engine.broker.orders import get_queued_orders

    orders = get_queued_orders()
    if not orders:
        console.print("[green]No queued orders.[/green]")
        return

    table = Table(title="Pre-Market Queued Orders", border_style="yellow")
    table.add_column("Ticker", style="bold")
    table.add_column("Action")
    table.add_column("Confidence")
    table.add_column("Horizon")

    for sig in orders:
        table.add_row(
            sig.ticker,
            sig.action.value,
            f"{sig.confidence:.0%}",
            sig.alpha_horizon.value,
        )

    console.print(table)
