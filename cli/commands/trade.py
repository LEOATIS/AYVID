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
        False,
        "--shadow",
        help="Run in shadow mode without IB (uses local OHLCV + simulated fills).",
    ),
    equity: float = typer.Option(
        1_000_000.0,
        "--equity",
        help="Starting NLV for shadow mode (ignored in IB mode — pulled from account).",
    ),
    interval: int = typer.Option(
        60,
        "--interval",
        help="Polling interval in seconds (e.g. 60 = once a minute, 86400 = once a day).",
    ),
    with_pipeline: bool = typer.Option(
        False,
        "--with-pipeline",
        help="Run the continuous ingestion pipeline (news→NER→graph→signals) alongside the runner.",
    ),
    pipeline_interval: int = typer.Option(
        15,
        "--pipeline-interval",
        help="Pipeline cycle interval in minutes (default 15).",
    ),
):
    """Start the live paper-trading runner with a daily equity-curve artifact.

    Writes to ``live/equity_curve.jsonl`` (one row per cycle), ``live/trades.jsonl``
    (one row per fill), and ``live/positions.json`` (current open positions).

    IB mode requires an IB Gateway running on the configured port (defaults
    to 7497 paper). Set PAPER_TRADING=true and IB_ACCOUNT in .env first.

    Shadow mode runs without any IB connection — useful while IB credentials
    are being provisioned. It still produces the same artifact format.

    Use --with-pipeline to run the continuous ingestion pipeline alongside
    the runner. This is the production configuration: the pipeline fetches
    news, runs NER, updates the Neo4j graph, detects shocks, and pre-computes
    signals into the signal book every --pipeline-interval minutes. The runner
    then consumes those signals each cycle.
    """

    async def _run():
        from execution_engine.paper_runner import run
        from shared.config import ExecutionMode, settings
        from shared.exceptions import IBKRDisconnectError

        # Mode resolution mirrors paper_runner.run(): explicit --shadow
        # wins, then settings.EXECUTION_MODE, then default ib_paper.
        if shadow:
            mode = "shadow"
        else:
            env_mode = getattr(settings, "EXECUTION_MODE", ExecutionMode.IB_PAPER)
            mode = env_mode.value if hasattr(env_mode, "value") else "ib_paper"
        pipeline_status = (
            f"[green]ON[/green] (every {pipeline_interval}min)" if with_pipeline else "[dim]off[/dim]"
        )
        console.print(
            Panel.fit(
                f"[bold]AYVID Paper Trader[/bold]\n"
                f"Mode: [cyan]{mode}[/cyan]\n"
                f"Interval: {interval}s\n"
                f"Initial equity: ${equity:,.0f}\n"
                f"Pipeline: {pipeline_status}\n\n"
                f"Artifacts:\n"
                f"  live/equity_curve.jsonl\n"
                f"  live/trades.jsonl\n"
                f"  live/positions.json\n\n"
                f"Press Ctrl-C to stop.",
                border_style="green",
            )
        )
        try:
            await run(
                shadow=shadow,
                initial_equity=equity,
                interval_seconds=interval,
                with_pipeline=with_pipeline,
                pipeline_interval_minutes=pipeline_interval,
            )
        except IBKRDisconnectError as exc:
            console.print(
                Panel.fit(
                    f"[bold red]IB Gateway connection failed[/bold red]\n\n"
                    f"{exc}\n\n"
                    f"Checks:\n"
                    f"  • IB Gateway / TWS running on {'127.0.0.1'}:7497 (paper)?\n"
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
        console.print(
            "[yellow]No equity curve yet — run 'ayvid trade paper-start' first.[/yellow]"
        )
        return

    rows = [json.loads(line) for line in eq_path.read_text().splitlines() if line.strip()]
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
    alpha_horizon: str = typer.Option(
        "3rd_order",
        "--horizon",
        help="Alpha horizon: 1st_order | 2nd_order | 3rd_order | 4th_order",
    ),
    backend_url: str | None = typer.Option(
        None,
        "--backend-url",
        help="FastAPI backend URL. Defaults to $AYVID_BACKEND_URL or "
        "shared.config.settings.AYVID_BACKEND_URL.",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="X-API-Key header value. Defaults to $AYVID_API_KEY or the "
        "configured API_KEY. Required when the backend has any "
        "keys registered.",
    ),
):
    """Manually execute a paper trade via the FastAPI backend.

    P0-B: this command submits an intent to the running backend rather
    than calling ``execute_manual_signal`` in-process. That keeps the
    manual trade inside the same lifespan as the autopilot — so it hits
    the same graph-freshness gate, the same sequence-sync gate, and the
    same kill-switch mmap writer. A direct in-process call would skip
    all three.

    Usage:
        ayvid trade execute AAPL LONG --confidence 0.8
        ayvid trade execute TSLA SHORT --backend-url http://staging:8001
    """
    import os

    import httpx

    from cli.exit_codes import (
        EXIT_BACKEND_AUTH_FAILED,
        EXIT_BACKEND_TIMEOUT,
        EXIT_BACKEND_UNREACHABLE,
        EXIT_DATA_PLANE_DESYNC,
        EXIT_GENERIC_ERROR,
        EXIT_USAGE_ERROR,
    )
    from shared.config import settings

    action = action.upper()
    if action not in ("LONG", "SHORT"):
        console.print("[red]Action must be LONG or SHORT[/red]")
        raise typer.Exit(EXIT_USAGE_ERROR)

    resolved_url = (
        backend_url or os.environ.get("AYVID_BACKEND_URL") or settings.AYVID_BACKEND_URL
    ).rstrip("/")

    resolved_key = api_key or os.environ.get("AYVID_API_KEY") or settings.API_KEY

    endpoint = f"{resolved_url}/api/v1/trade/manual"
    headers = {"Content-Type": "application/json"}
    if resolved_key:
        headers["X-API-Key"] = resolved_key

    payload = {
        "ticker": ticker,
        "action": action,
        "confidence": confidence,
        "alpha_horizon": alpha_horizon,
    }

    console.print(
        f"\n[bold]Submitting:[/bold] {action} {ticker} "
        f"(confidence={confidence:.0%}, horizon={alpha_horizon})\n"
        f"[dim]→ POST {endpoint}[/dim]\n"
    )

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(endpoint, headers=headers, json=payload)
    except httpx.ConnectError as exc:
        console.print(
            Panel(
                f"[bold red]Cannot reach backend at {endpoint}[/bold red]\n\n"
                f"{exc}\n\n"
                f"Checks:\n"
                f"  • Is the FastAPI service running? "
                f"[cyan]ayvid system serve[/cyan]\n"
                f"  • Is $AYVID_BACKEND_URL correct?\n"
                f"  • If running remotely, is the port reachable?",
                title="[bold]Connection Error[/bold]",
                border_style="red",
            )
        )
        raise typer.Exit(code=EXIT_BACKEND_UNREACHABLE)
    except httpx.TimeoutException as exc:
        console.print(f"[red]Backend timeout: {exc}[/red]")
        raise typer.Exit(code=EXIT_BACKEND_TIMEOUT)
    except httpx.HTTPError as exc:
        console.print(f"[red]HTTP transport error: {exc}[/red]")
        raise typer.Exit(code=EXIT_BACKEND_UNREACHABLE)

    if response.status_code == 403:
        console.print(
            Panel(
                "[bold red]Authentication rejected (HTTP 403)[/bold red]\n\n"
                "The backend has API keys registered but the CLI sent none "
                "or an invalid one. Set AYVID_API_KEY in your shell env or "
                "pass --api-key.",
                title="[bold]Auth Error[/bold]",
                border_style="red",
            )
        )
        raise typer.Exit(code=EXIT_BACKEND_AUTH_FAILED)

    if response.status_code == 409:
        # Data plane desync (GraphStaleError / SequenceValidationError)
        console.print(
            Panel(
                f"[bold red]Trade rejected — data plane desync[/bold red]\n\n"
                f"{response.json().get('detail', response.text)}\n\n"
                f"The autopilot registry is out of sync across sources. "
                f"Wait for the next ingestion cycle or run "
                f"[cyan]ayvid system health[/cyan] to inspect.",
                title="[bold]Rejected[/bold]",
                border_style="red",
            )
        )
        raise typer.Exit(code=EXIT_DATA_PLANE_DESYNC)

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        console.print(f"[red]Backend error HTTP {response.status_code}: {detail}[/red]")
        raise typer.Exit(code=EXIT_GENERIC_ERROR)

    result = response.json()
    status = str(result.get("status", "unknown")).upper()
    console.print(
        Panel(
            f"  Status: [bold]{status}[/bold]\n"
            f"  Ticker: {result.get('ticker', ticker)}\n"
            f"  Direction: {result.get('direction', action)}\n"
            f"  Intent ID: {result.get('intent_id', '?')}",
            title="[bold]Trade Result[/bold]",
            border_style="green" if status == "FILLED" else "yellow",
        )
    )


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
            compute_expected_returns,
            compute_rolling_volatility,
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

        console.print(
            Panel(
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
            )
        )

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
