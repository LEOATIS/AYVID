"""Trading signals & execution CLI commands."""
from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
trade_app = typer.Typer(help="Trading signals & execution", no_args_is_help=True)


@trade_app.command("paper-start")
def paper_start(
    shadow: bool = typer.Option(
        False, "--shadow",
        help="Run in shadow mode without IB (uses local OHLCV + simulated fills).",
    ),
    equity: float = typer.Option(
        1_000_000.0, "--equity",
        help="Starting NLV for shadow mode (ignored in IB mode — pulled from account).",
    ),
    interval: int = typer.Option(
        60, "--interval",
        help="Polling interval in seconds (e.g. 60 = once a minute, 86400 = once a day).",
    ),
):
    """Start the live paper-trading runner with a daily equity-curve artifact.

    Writes to ``live/equity_curve.jsonl`` (one row per cycle), ``live/trades.jsonl``
    (one row per fill), and ``live/positions.json`` (current open positions).

    IB mode requires an IB Gateway running on the configured port (defaults
    to 7497 paper). Set PAPER_TRADING=true and IB_ACCOUNT in .env first.

    Shadow mode runs without any IB connection — useful while IB credentials
    are being provisioned. It still produces the same artifact format.
    """
    async def _run():
        from execution_engine.paper_runner import run
        from shared.exceptions import IBKRDisconnectError

        mode = "shadow" if shadow else "ib_paper"
        console.print(
            Panel.fit(
                f"[bold]AYVID Paper Trader[/bold]\n"
                f"Mode: [cyan]{mode}[/cyan]\n"
                f"Interval: {interval}s\n"
                f"Initial equity: ${equity:,.0f}\n\n"
                f"Artifacts:\n"
                f"  live/equity_curve.jsonl\n"
                f"  live/trades.jsonl\n"
                f"  live/positions.json\n\n"
                f"Press Ctrl-C to stop.",
                border_style="green",
            )
        )
        try:
            await run(shadow=shadow, initial_equity=equity, interval_seconds=interval)
        except IBKRDisconnectError as exc:
            console.print(
                Panel.fit(
                    f"[bold red]IB Gateway connection failed[/bold red]\n\n"
                    f"{exc}\n\n"
                    f"Checks:\n"
                    f"  • IB Gateway / TWS running on {  '127.0.0.1' }:7497 (paper)?\n"
                    f"  • API enabled in Gateway → Configure → Settings → API?\n"
                    f"  • IB_HOST / IB_PORT / IB_CLIENT_ID set in .env?\n\n"
                    f"Or run shadow mode (no IB needed):\n"
                    f"  [cyan]ayvid trade paper-start --shadow --equity {int(equity)}[/cyan]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Paper runner stopped.[/yellow]")


@trade_app.command("paper-status")
def paper_status():
    """Show the most recent paper-trading equity snapshot."""
    import json
    from pathlib import Path

    eq_path = Path("live/equity_curve.jsonl")
    if not eq_path.exists():
        console.print("[yellow]No equity curve yet — run 'ayvid trade paper-start' first.[/yellow]")
        return

    rows = [
        json.loads(line)
        for line in eq_path.read_text().splitlines()
        if line.strip()
    ]
    if not rows:
        console.print("[yellow]Equity curve file is empty.[/yellow]")
        return

    last = rows[-1]
    first = rows[0]
    table = Table(title="AYVID Live Paper Trading", border_style="green")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right", style="bold cyan")
    table.add_row("Mode", str(last.get("mode", "?")))
    table.add_row("Snapshots", str(len(rows)))
    table.add_row("Started", str(first.get("timestamp", "?")))
    table.add_row("Last update", str(last.get("timestamp", "?")))
    table.add_row("Initial equity", f"${first.get('equity', 0):,.2f}")
    table.add_row("Current equity", f"${last.get('equity', 0):,.2f}")
    table.add_row("Cash", f"${last.get('cash', 0):,.2f}")
    table.add_row("Position value", f"${last.get('position_value', 0):,.2f}")
    table.add_row("Open positions", str(last.get("open_positions", 0)))
    table.add_row("Cumulative PnL", f"${last.get('cumulative_pnl', 0):+,.2f}")
    table.add_row("Cumulative return", f"{last.get('cumulative_return_pct', 0):+.3f}%")
    console.print(table)


@trade_app.command("paper-render")
def paper_render(
    out: str = typer.Option("live/equity_curve.png", help="Output image path."),
):
    """Render the live paper equity curve as a PNG (or SVG fallback)."""
    from pathlib import Path
    from execution_engine.paper_runner import render_curve

    path = render_curve(out_path=Path(out))
    console.print(f"[green]Wrote {path}[/green]")


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
