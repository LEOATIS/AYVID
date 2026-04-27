"""AYVID end-to-end demo — runs the full pipeline and produces a formatted report.

Usage: python -m scripts.demo   (or:  make demo)
"""

from __future__ import annotations

import asyncio
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


async def run_demo() -> int:
    t0 = time.monotonic()
    console.print(
        Panel.fit(
            "[bold cyan]AYVID[/bold cyan]  —  Asymmetric Yield Via Intelligent Decisions\n"
            "[dim]End-to-end demo: ingest → NER → graph → analysis → backtest[/dim]",
            border_style="cyan",
        )
    )

    # ── Step 1: System status ────────────────────────────────────
    console.print("\n[bold]1/5  Service connectivity[/bold]")
    from shared.config import settings

    checks: list[tuple[str, bool]] = []
    checks.append(("OpenAI API Key", bool(settings.OPENAI_API_KEY)))
    checks.append(
        (
            "Neo4j URI",
            settings.NEO4J_URI != "bolt://localhost:7687" or bool(settings.NEO4J_PASSWORD),
        )
    )
    checks.append(("Qdrant URL", bool(settings.QDRANT_URL)))
    checks.append(("Paper Trading", settings.PAPER_TRADING))

    for name, ok in checks:
        icon = "[green]OK[/green]" if ok else "[yellow]SKIP[/yellow]"
        console.print(f"  {icon}  {name}")

    # ── Step 2: Universe snapshot ────────────────────────────────
    console.print("\n[bold]2/5  Investment universe[/bold]")
    from data_primitives.extractors.universe_builder import build_full_universe

    snap = await build_full_universe()
    console.print(
        f"  {snap.total:,} instruments across {len(snap.exchanges)} exchanges, "
        f"{len(snap.countries)} countries"
    )

    # ── Step 3: Ingest a few tickers ─────────────────────────────
    console.print("\n[bold]3/5  Data ingestion (5 tickers)[/bold]")
    demo_tickers = ["AAPL", "TSM", "NVDA", "ASML", "MSFT"]
    from data_primitives.extractors.openbb_client import fetch_ohlcv
    from data_primitives.loaders.questdb_writer import store_bars_local

    total_bars = 0
    for ticker in demo_tickers:
        try:
            bars = await fetch_ohlcv(ticker, days=30)
            if bars:
                await store_bars_local(ticker, bars)
                total_bars += len(bars)
                console.print(f"  [green]{ticker}[/green]: {len(bars)} bars")
            else:
                console.print(f"  [yellow]{ticker}[/yellow]: no data")
        except Exception as exc:
            console.print(f"  [red]{ticker}[/red]: {exc!s:.50}")
    console.print(f"  Total: {total_bars} bars stored")

    # ── Step 4: Run backtest ─────────────────────────────────────
    console.print("\n[bold]4/5  Historical backtest (60 days)[/bold]")
    from backtesting.engine import run_backtest

    result = await run_backtest(tickers=demo_tickers, days=60, initial_equity=1_000_000.0)

    table = Table(border_style="green", show_header=False, pad_edge=False)
    table.add_column("Metric", style="bold", min_width=20)
    table.add_column("Value", justify="right", style="cyan")
    table.add_row("Period", f"{result.start_date.date()} → {result.end_date.date()}")
    table.add_row("Total Return", f"{result.total_return_pct:+.2f}%")
    table.add_row("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
    table.add_row("Max Drawdown", f"{result.max_drawdown_pct:.1f}%")
    table.add_row("Win Rate", f"{result.win_rate:.0f}% ({result.total_trades} trades)")
    table.add_row("Profit Factor", f"{result.profit_factor:.2f}")
    console.print(table)

    # ── Step 5: Test run ─────────────────────────────────────────
    console.print("\n[bold]5/5  Test suite[/bold]")
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        capture_output=True,
        text=True,
        cwd=".",
    )
    last_line = proc.stdout.strip().split("\n")[-1] if proc.stdout.strip() else "no output"
    icon = "[green]PASS[/green]" if proc.returncode == 0 else "[red]FAIL[/red]"
    console.print(f"  {icon}  {last_line}")

    elapsed = time.monotonic() - t0
    console.print(
        Panel.fit(
            f"[bold green]Demo complete[/bold green] in {elapsed:.1f}s\n"
            f"[dim]{total_bars} bars ingested, {result.total_trades} trades simulated, "
            f"tests {'passed' if proc.returncode == 0 else 'failed'}[/dim]",
            border_style="green",
        )
    )
    return 0


def main() -> None:
    sys.exit(asyncio.run(run_demo()))


if __name__ == "__main__":
    main()
