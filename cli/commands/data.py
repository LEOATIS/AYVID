"""Data ingestion CLI commands."""
from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.panel import Panel

console = Console()
data_app = typer.Typer(
    help=(
        "Market data, universe scanning, backtesting, and pipeline ingestion.\n\n"
        "Includes two-track institutional backtest (Index F&O + Equity Investment),\n"
        "global universe builder, world anomaly scanner, and full ingestion pipeline."
    ),
    no_args_is_help=True,
)


# ── Universe Commands ─────────────────────────────────────────

@data_app.command("universe")
def universe():
    """Build and display the full global investment universe."""
    async def _run():
        from data_primitives.extractors.universe_builder import build_full_universe

        console.print("\n[bold]Building Global Investment Universe...[/bold]\n")
        snap = await build_full_universe()

        table = Table(title="AYVID Global Universe", border_style="cyan")
        table.add_column("Asset Class", style="bold")
        table.add_column("Count", justify="right", style="bold cyan")

        table.add_row("Equities (Global)", f"{snap.equities:,}")
        table.add_row("ETFs", f"{snap.etfs:,}")
        table.add_row("Market Indices", f"{snap.indices:,}")
        table.add_row("Futures (Commodities + Rates)", f"{snap.futures:,}")
        table.add_row("FX Pairs (Major + EM)", f"{snap.fx_pairs:,}")
        table.add_row("Crypto Assets", f"{snap.crypto:,}")
        table.add_row("[bold]TOTAL[/bold]", f"[bold]{snap.total:,}[/bold]")
        console.print(table)

        console.print(f"\n  Exchanges: [cyan]{', '.join(snap.exchanges[:20])}[/cyan]")
        console.print(f"  Countries: [cyan]{', '.join(snap.countries[:20])}[/cyan]\n")

    asyncio.run(_run())


@data_app.command("refresh-universe")
def refresh_universe():
    """Discover and expand the investment universe from live data sources (S&P 500, FMP screener)."""
    async def _run():
        from data_primitives.extractors.universe_builder import build_full_universe

        console.print("\n[bold]Discovering global instruments from live sources...[/bold]\n")
        snap = await build_full_universe(run_discovery=True)

        table = Table(title="Expanded Universe", border_style="green")
        table.add_column("Asset Class", style="bold")
        table.add_column("Count", justify="right", style="bold cyan")

        table.add_row("Equities (Global)", f"{snap.equities:,}")
        table.add_row("ETFs", f"{snap.etfs:,}")
        table.add_row("Market Indices", f"{snap.indices:,}")
        table.add_row("Futures (Commodities + Rates)", f"{snap.futures:,}")
        table.add_row("FX Pairs (Major + EM)", f"{snap.fx_pairs:,}")
        table.add_row("Crypto Assets", f"{snap.crypto:,}")
        table.add_row("[bold]TOTAL[/bold]", f"[bold]{snap.total:,}[/bold]")
        console.print(table)

        console.print(f"\n  Exchanges: [cyan]{', '.join(snap.exchanges[:20])}[/cyan]")
        console.print(f"  Countries: [cyan]{', '.join(snap.countries[:20])}[/cyan]\n")

    asyncio.run(_run())


@data_app.command("world-scan")
def world_scan(
    tickers: list[str] = typer.Argument(
        default=None,
        help="Tickers to scan (omit for supply-chain-critical universe)"
    ),
    sigma: float = typer.Option(2.0, help="Sigma threshold for anomaly detection"),
    days: int = typer.Option(30, help="Lookback window"),
):
    """Scan global markets for anomalies: price shocks, volume spikes, gaps."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_multi_ohlcv
        from data_primitives.extractors.market_scanner import scan_universe
        from data_primitives.extractors.universe_builder import get_supply_chain_critical_tickers

        scan_tickers = tickers or get_supply_chain_critical_tickers()
        console.print(f"\n[bold]Scanning {len(scan_tickers)} instruments for anomalies...[/bold]\n")

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), console=console,
        ) as progress:
            task = progress.add_task("Fetching price data...", total=None)
            data = await fetch_multi_ohlcv(scan_tickers, days=days)
            progress.update(task, description=f"Scanning {len(data)} instruments...")
            results = await scan_universe(data, sigma_threshold=sigma)

        if not results:
            console.print("[green]No anomalies detected.[/green]")
            return

        table = Table(title=f"Market Anomalies ({len(results)} detected)", border_style="red")
        table.add_column("Symbol", style="bold")
        table.add_column("Type", justify="center")
        table.add_column("Severity", justify="right")
        table.add_column("Price Chg", justify="right")
        table.add_column("Description")

        for r in results[:25]:
            sev_style = "red" if r.severity > 3 else ("yellow" if r.severity > 2 else "dim")
            table.add_row(
                r.symbol,
                r.event_type.value,
                f"[{sev_style}]{r.severity:.2f}[/{sev_style}]",
                f"{r.price_change_pct:+.2f}%" if r.price_change_pct else "-",
                r.description[:60],
            )
        console.print(table)

    asyncio.run(_run())


@data_app.command("indices")
def indices(
    days: int = typer.Option(10, help="Lookback days"),
):
    """Show performance of major world market indices."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_index_historical
        from data_primitives.extractors.universe_builder import WORLD_INDICES

        all_indices = list(WORLD_INDICES.items())
        console.print(f"\n[bold]Fetching {len(all_indices)} world indices...[/bold]\n")

        table = Table(title="World Market Indices", border_style="cyan")
        table.add_column("Symbol", style="dim")
        table.add_column("Index", style="bold")
        table.add_column("Country", justify="center")
        table.add_column("Last", justify="right")
        table.add_column("Chg%", justify="right")

        import math

        for symbol, (name, country, _) in all_indices:
            try:
                bars = await fetch_index_historical(symbol, days=days)
                valid = [b for b in bars if not math.isnan(b.close)]
                if valid and len(valid) >= 2:
                    last = valid[-1].close
                    change = (valid[-1].close / valid[0].close - 1) * 100
                    chg_style = "green" if change >= 0 else "red"
                    table.add_row(
                        symbol, name[:35], country,
                        f"{last:,.2f}",
                        f"[{chg_style}]{change:+.2f}%[/{chg_style}]",
                    )
                else:
                    table.add_row(symbol, name[:35], country, "-", "-")
            except Exception:
                table.add_row(symbol, name[:35], country, "[red]err[/red]", "-")

        console.print(table)

    asyncio.run(_run())


@data_app.command("fx")
def fx():
    """Show major FX pair rates."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_fx_historical

        major_pairs = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "USDCNY", "USDINR"]
        console.print(f"\n[bold]Fetching {len(major_pairs)} FX pairs...[/bold]\n")

        table = Table(title="Major FX Pairs", border_style="blue")
        table.add_column("Pair", style="bold")
        table.add_column("Rate", justify="right")
        table.add_column("Change", justify="right")

        import math

        for pair in major_pairs:
            try:
                bars = await fetch_fx_historical(pair, days=5)
                valid = [b for b in bars if not math.isnan(b.close)]
                if valid and len(valid) >= 2:
                    rate = valid[-1].close
                    change = (valid[-1].close / valid[-2].close - 1) * 100
                    chg_style = "green" if change >= 0 else "red"
                    table.add_row(pair, f"{rate:.4f}", f"[{chg_style}]{change:+.3f}%[/{chg_style}]")
                else:
                    table.add_row(pair, "-", "-")
            except Exception:
                table.add_row(pair, "-", "-")

        console.print(table)

    asyncio.run(_run())


@data_app.command("crypto")
def crypto():
    """Show top crypto asset prices."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_crypto_historical

        assets = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK"]
        console.print(f"\n[bold]Fetching {len(assets)} crypto assets...[/bold]\n")

        table = Table(title="Crypto Markets", border_style="magenta")
        table.add_column("Asset", style="bold")
        table.add_column("Price", justify="right")
        table.add_column("24h Chg", justify="right")

        for symbol in assets:
            try:
                bars = await fetch_crypto_historical(symbol, days=5)
                if bars and len(bars) >= 2:
                    price = bars[-1].close
                    change = (bars[-1].close / bars[-2].close - 1) * 100
                    chg_style = "green" if change >= 0 else "red"
                    table.add_row(symbol, f"${price:,.2f}", f"[{chg_style}]{change:+.2f}%[/{chg_style}]")
            except Exception:
                table.add_row(symbol, "-", "-")

        console.print(table)

    asyncio.run(_run())


@data_app.command("commodities")
def commodities():
    """Show major commodity futures prices."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_commodity_historical

        commodities_list = [
            ("CL", "Crude Oil WTI"), ("BZ", "Brent Crude"), ("NG", "Natural Gas"),
            ("GC", "Gold"), ("SI", "Silver"), ("HG", "Copper"),
            ("ZC", "Corn"), ("ZS", "Soybeans"), ("ZW", "Wheat"),
        ]
        console.print(f"\n[bold]Fetching {len(commodities_list)} commodity futures...[/bold]\n")

        table = Table(title="Commodity Futures", border_style="yellow")
        table.add_column("Symbol", style="dim")
        table.add_column("Commodity", style="bold")
        table.add_column("Price", justify="right")
        table.add_column("Change", justify="right")

        for symbol, name in commodities_list:
            try:
                bars = await fetch_commodity_historical(symbol, days=10)
                if bars and len(bars) >= 2:
                    price = bars[-1].close
                    change = (bars[-1].close / bars[-2].close - 1) * 100
                    chg_style = "green" if change >= 0 else "red"
                    table.add_row(symbol, name, f"${price:,.2f}", f"[{chg_style}]{change:+.2f}%[/{chg_style}]")
            except Exception:
                table.add_row(symbol, name, "-", "-")

        console.print(table)

    asyncio.run(_run())


@data_app.command("earnings")
def earnings():
    """Show upcoming earnings announcements."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_earnings_calendar

        events = await fetch_earnings_calendar(days_ahead=7)
        if not events:
            console.print("[yellow]No upcoming earnings found.[/yellow]")
            return

        table = Table(title="Upcoming Earnings (Next 7 Days)", border_style="magenta")
        table.add_column("Date", style="dim")
        table.add_column("Ticker", style="bold")
        table.add_column("Company")
        table.add_column("EPS Est.", justify="right")
        table.add_column("Revenue Est.", justify="right")

        import yfinance as yf

        symbols = [e.get("symbol", "") for e in events[:30] if e.get("symbol")]
        name_map: dict[str, str] = {}
        if symbols:
            tickers = yf.Tickers(" ".join(symbols))
            for sym in symbols:
                try:
                    info = tickers.tickers[sym].info
                    name_map[sym] = info.get("shortName") or info.get("longName") or ""
                except Exception:
                    name_map[sym] = ""

        for e in events[:30]:
            rev = e.get("revenue_consensus")
            rev_str = f"${rev / 1e9:.1f}B" if rev else "-"
            eps = e.get("eps_consensus")
            eps_str = f"${eps:.2f}" if eps is not None else "-"
            sym = str(e.get("symbol", ""))
            company = e.get("name") or name_map.get(sym, "")
            table.add_row(
                str(e.get("report_date", e.get("date", ""))),
                sym,
                company[:40],
                eps_str,
                rev_str,
            )
        console.print(table)

    asyncio.run(_run())


@data_app.command("sectors")
def sectors():
    """Show US sector performance."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_sector_performance

        data = await fetch_sector_performance()
        if not data:
            console.print("[yellow]Sector data unavailable.[/yellow]")
            return

        table = Table(title="US Sector Performance (SPDR ETFs)", border_style="blue")
        table.add_column("Sector", style="bold")
        table.add_column("ETF", style="dim")
        table.add_column("Last", justify="right")
        table.add_column("1D Chg%", justify="right")
        table.add_column("30D Chg%", justify="right")

        for row in data:
            chg_1d = row.get("change_1d", 0)
            chg_30d = row.get("change_30d", 0)
            s1 = "green" if chg_1d >= 0 else "red"
            s30 = "green" if chg_30d >= 0 else "red"
            table.add_row(
                row.get("sector", ""),
                row.get("etf", ""),
                f"${row.get('last', 0):,.2f}",
                f"[{s1}]{chg_1d:+.2f}%[/{s1}]",
                f"[{s30}]{chg_30d:+.2f}%[/{s30}]",
            )

        console.print(table)

    asyncio.run(_run())


@data_app.command("ingest")
def ingest(
    tickers: list[str] = typer.Argument(help="Tickers to ingest (e.g., AAPL TSLA MSFT)"),
    days: int = typer.Option(90, help="Number of days of history to fetch"),
):
    """Ingest OHLCV data, news, and features for the given tickers."""
    async def _run():
        from backend.services.orchestrator import ingest_market_data
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Ingesting market data...", total=len(tickers))
            results = await ingest_market_data(tickers)
            for ticker, count in results.items():
                progress.advance(task)
                status = f"[green]{count} bars[/green]" if count > 0 else "[red]failed[/red]"
                console.print(f"  {ticker}: {status}")

        console.print(f"\n[bold green]Ingestion complete.[/bold green] {sum(results.values())} total bars stored.")

    asyncio.run(_run())


@data_app.command("prices")
def prices(
    ticker: str = typer.Argument(help="Ticker symbol"),
    days: int = typer.Option(30, help="Number of days"),
):
    """Fetch and display recent OHLCV prices for a ticker."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_ohlcv

        bars = await fetch_ohlcv(ticker, days=days)
        table = Table(title=f"OHLCV — {ticker} (last {days} days)", border_style="blue")
        table.add_column("Date", style="dim")
        table.add_column("Open", justify="right")
        table.add_column("High", justify="right", style="green")
        table.add_column("Low", justify="right", style="red")
        table.add_column("Close", justify="right", style="bold")
        table.add_column("Volume", justify="right", style="dim")

        for bar in bars[-20:]:
            table.add_row(
                bar.timestamp.strftime("%Y-%m-%d"),
                f"${bar.open:.2f}",
                f"${bar.high:.2f}",
                f"${bar.low:.2f}",
                f"${bar.close:.2f}",
                f"{bar.volume:,}",
            )
        console.print(table)

    asyncio.run(_run())


@data_app.command("news")
def news(
    ticker: str = typer.Argument(help="Ticker symbol"),
    limit: int = typer.Option(10, help="Number of articles"),
):
    """Fetch and display recent news for a ticker."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_company_news

        items = await fetch_company_news(ticker, limit=limit)
        for item in items:
            style = "yellow" if "shock" in item.title.lower() else "white"
            console.print(Panel(
                f"[dim]{item.timestamp.strftime('%Y-%m-%d %H:%M')}[/dim]\n"
                f"[{style}]{item.title}[/{style}]\n"
                f"[dim]{item.source}[/dim]"
                + (f"\n[dim]{item.url}[/dim]" if item.url else ""),
                border_style="dim",
            ))

    asyncio.run(_run())


@data_app.command("verify")
def verify():
    """Verify all data source connections (OpenBB, QuestDB, etc.)."""
    async def _run():
        console.print("\n[bold]Data Primitive Verification Suite[/bold]\n")

        checks = []

        # OpenBB / YFinance
        try:
            from data_primitives.extractors.openbb_client import fetch_ohlcv
            bars = await fetch_ohlcv("AAPL", days=5)
            checks.append(("OpenBB OHLCV (YFinance)", True, f"{len(bars)} bars"))
        except Exception as e:
            checks.append(("OpenBB OHLCV (YFinance)", False, str(e)[:60]))

        # OpenBB News
        try:
            from data_primitives.extractors.openbb_client import fetch_company_news
            news = await fetch_company_news("AAPL", limit=2)
            checks.append(("OpenBB News", True, f"{len(news)} articles"))
        except Exception as e:
            checks.append(("OpenBB News", False, str(e)[:60]))

        # SEC Filings (FMP)
        try:
            from data_primitives.extractors.openbb_client import fetch_sec_filings
            filings = await fetch_sec_filings("AAPL", limit=2)
            checks.append(("SEC Filings (FMP)", True, f"{len(filings)} filings"))
        except Exception as e:
            checks.append(("SEC Filings (FMP)", False, str(e)[:60]))

        table = Table(border_style="blue")
        table.add_column("Source", style="bold")
        table.add_column("Status", justify="center")
        table.add_column("Details")
        for name, ok, detail in checks:
            status = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
            table.add_row(name, status, detail)

        console.print(table)

    asyncio.run(_run())


@data_app.command("calendar")
def calendar():
    """Show upcoming macroeconomic events."""
    async def _run():
        from data_primitives.extractors.openbb_client import fetch_economic_calendar

        events = await fetch_economic_calendar(days_ahead=14)
        if not events:
            console.print("[yellow]No upcoming events found.[/yellow]")
            return

        table = Table(title="Macro Calendar (Next 14 Days)", border_style="yellow")
        table.add_column("Date", style="dim")
        table.add_column("Event", style="bold")
        table.add_column("Country", justify="center")

        for event in events[:20]:
            table.add_row(
                str(event.get("date", "")),
                str(event.get("event", event.get("description", "")))[:60],
                str(event.get("country", "")),
            )
        console.print(table)

    asyncio.run(_run())


# ── SEC Filings & Fundamentals ────────────────────────────────

@data_app.command("filings")
def filings(symbol: str = typer.Argument(..., help="Ticker to analyze")):
    """Analyze SEC filings for supply chain intelligence (GPT-4o powered)."""
    async def _run():
        from data_primitives.extractors.filing_analyzer import analyze_sec_filing

        console.print(f"\n[bold]Analyzing SEC filings for {symbol}...[/bold]\n")
        result = await analyze_sec_filing(symbol)

        if result.get("error"):
            console.print(f"[yellow]No filings available for {symbol}[/yellow]")
            return

        table = Table(title=f"{symbol} SEC Filing Analysis", border_style="green")
        table.add_column("Field", style="bold")
        table.add_column("Details", max_width=80)

        if result.get("risk_factors"):
            table.add_row("Risk Factors", "\n".join(f"- {r}" for r in result["risk_factors"][:5]))
        if result.get("supply_chain_dependencies"):
            deps = result["supply_chain_dependencies"]
            dep_str = "\n".join(
                f"- {d.get('supplier', '?')}: {d.get('component', '?')} ({d.get('criticality', '?')})"
                for d in deps[:5]
            )
            table.add_row("Supply Chain Deps", dep_str)
        if result.get("revenue_segments"):
            segs = result["revenue_segments"]
            seg_str = "\n".join(
                f"- {s.get('segment', '?')}: ~{s.get('revenue_pct', '?')}% ({s.get('growth_trend', '?')})"
                for s in segs[:5]
            )
            table.add_row("Revenue Segments", seg_str)
        if result.get("key_customers"):
            table.add_row("Key Customers", ", ".join(result["key_customers"]))
        if result.get("summary"):
            table.add_row("Summary", result["summary"])

        console.print(table)

    asyncio.run(_run())


@data_app.command("profile")
def build_profile(symbol: str = typer.Argument(..., help="Ticker to profile")):
    """Build comprehensive fundamental profile (filings + earnings + ratios)."""
    async def _run():
        from data_primitives.extractors.filing_analyzer import build_fundamental_profile

        console.print(f"\n[bold]Building fundamental profile for {symbol}...[/bold]\n")
        result = await build_fundamental_profile(symbol)

        fa = result.get("filing_analysis", {})
        if fa and not fa.get("error"):
            deps = fa.get("supply_chain_dependencies", [])
            dep_text = "\n".join(
                f"  {d.get('supplier','?')} -> {d.get('component','?')} [{d.get('criticality','?')}]"
                for d in deps[:5]
            )
            console.print(Panel(dep_text or "No data", title="Supply Chain", border_style="green"))

        ea = result.get("earnings_analysis", {})
        if ea and not ea.get("error"):
            rev = ea.get("revenue_assessment", {})
            guid = ea.get("guidance_changes", {})
            sent = ea.get("sentiment", {})
            text = (
                f"Revenue: {rev.get('beat_or_miss','?')} ({rev.get('magnitude','')})\n"
                f"Guidance: {guid.get('direction','?')}\n"
                f"Sentiment: {sent.get('overall','?')}"
            )
            console.print(Panel(text, title="Earnings", border_style="yellow"))

        ratios = result.get("financial_ratios", {})
        if ratios and not ratios.get("error"):
            key_ratios = ["pe_ratio", "return_on_equity", "debt_equity_ratio", "current_ratio"]
            text = "\n".join(f"  {k}: {ratios.get(k, 'N/A')}" for k in key_ratios if k in ratios)
            if text:
                console.print(Panel(text, title="Financial Ratios", border_style="cyan"))

        sc = result.get("supply_chain_summary", {})
        if sc.get("dependencies"):
            console.print(f"\n[bold green]Supply Chain Dependencies:[/bold green] {sc['dependency_count']}")

    asyncio.run(_run())


@data_app.command("ingest-intraday")
def ingest_intraday(
    tickers: list[str] = typer.Argument(help="Tickers to fetch intraday data for"),
    interval: str = typer.Option("5m", help="Bar interval: 1m, 5m, 15m, 30m, 1h"),
    days: int = typer.Option(5, help="Number of days of intraday history"),
):
    """Fetch and store intraday (M1/M5/M15) OHLCV data for the given tickers."""
    async def _run():
        import json
        from pathlib import Path
        from data_primitives.extractors.openbb_client import fetch_intraday_batch

        console.print(f"\n[bold]Fetching {interval} intraday data for {len(tickers)} tickers ({days}d)...[/bold]\n")
        data = await fetch_intraday_batch(tickers, interval=interval, days=days)

        intraday_dir = Path("data_store/intraday")
        intraday_dir.mkdir(parents=True, exist_ok=True)

        total_bars = 0
        for sym, bars in data.items():
            if not bars:
                continue
            path = intraday_dir / f"{sym}_{interval}.jsonl"
            with open(path, "w") as f:
                for bar in bars:
                    f.write(bar.model_dump_json() + "\n")
            total_bars += len(bars)
            console.print(f"  {sym}: [green]{len(bars)} bars[/green] -> {path}")

        console.print(f"\n[bold green]Done.[/bold green] {total_bars} total bars stored for {len(data)} tickers.")

    asyncio.run(_run())


@data_app.command("pipeline")
def run_pipeline():
    """Run one full ingestion cycle (news -> NER -> graph -> analysis)."""
    async def _run():
        from data_primitives.pipeline.continuous_worker import run_ingestion_cycle

        console.print("\n[bold]Running full ingestion pipeline...[/bold]\n")
        result = await run_ingestion_cycle(run_analysis=True)

        table = Table(title="Pipeline Cycle Results", border_style="magenta")
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right", style="bold cyan")

        table.add_row("News Fetched", str(result.get("news_fetched", 0)))
        table.add_row("Events Created", str(result.get("events_created", 0)))
        table.add_row("Bars Ingested", str(result.get("bars_ingested", 0)))
        table.add_row("Signals Generated", str(result.get("signals_generated", 0)))
        table.add_row("Tier 1 Tickers", str(result.get("tier1_size", 0)))
        table.add_row("Tier 2 Active", str(result.get("tier2_size", 0)))
        table.add_row("Duration", f"{result.get('elapsed_seconds', 0):.1f}s")

        if result.get("errors"):
            table.add_row("Errors", "\n".join(result["errors"]))

        console.print(table)

    asyncio.run(_run())


@data_app.command("scenarios")
def list_scenarios():
    """List available named historical shock scenarios for targeted backtesting."""
    from backtesting.engine import SHOCK_SCENARIOS

    table = Table(title="Available Shock Scenarios", border_style="magenta")
    table.add_column("Key", style="bold")
    table.add_column("Name")
    table.add_column("Period", justify="center")
    table.add_column("Tickers", justify="right")
    table.add_column("Indices", justify="right")
    table.add_column("Description", max_width=55)

    for key, sc in SHOCK_SCENARIOS.items():
        table.add_row(
            key,
            sc["name"],
            f"{sc['start_date']} → {sc['end_date']}",
            str(len(sc["tickers"])),
            str(len(sc.get("indices", []))),
            sc["description"],
        )
    console.print(table)
    console.print("\n[dim]Use: ayvid data backtest --scenario <key>[/dim]\n")


@data_app.command("backtest")
def backtest(
    days: int = typer.Option(60, help="Number of days to backtest"),
    equity: float = typer.Option(1_000_000.0, help="Initial equity ($)"),
    stop_loss: float = typer.Option(3.0, help="Stop-loss percentage"),
    take_profit: float = typer.Option(6.0, help="Take-profit percentage"),
    universe: str = typer.Option(
        "auto",
        help="Ticker universe: auto (local data), us_mega, global, supply_chain, etfs, or comma-separated tickers",
    ),
    fresh: bool = typer.Option(
        False, "--fresh", "-f",
        help="Refresh ALL data before backtesting: fetch fresh indices, screen best equities, update OHLCV",
    ),
    start_date: str = typer.Option(
        None, "--start-date",
        help="Explicit start date (ISO format: 2021-03-20). Overrides --days.",
    ),
    end_date: str = typer.Option(
        None, "--end-date",
        help="Explicit end date (ISO format: 2021-04-15). Defaults to today.",
    ),
    scenario: str = typer.Option(
        None, "--scenario", "-s",
        help="Named shock scenario (e.g. suez_canal_2021). Use 'ayvid data scenarios' to list.",
    ),
):
    """Run the two-track institutional backtest (AI-native pipeline).

    Track 1 — INDEX F&O: Trades index futures and options on NIFTY, S&P 500,
    DAX, FTSE, Nikkei, and other world indices with proper multipliers.

    Track 2 — EQUITY INVESTMENT: Stock positions driven by supply chain
    intelligence, deep value, and trend following.

    Use --fresh / -f to refresh all data before running (recommended).
    Use --start-date / --end-date for arbitrary date ranges.
    Use --scenario for named historical shock scenarios.
    """
    async def _run():
        from pathlib import Path

        bt_start_date = start_date
        bt_end_date = end_date
        ticker_list: list[str] | None = None

        if scenario:
            from backtesting.engine import run_scenario_backtest, SHOCK_SCENARIOS
            if scenario not in SHOCK_SCENARIOS:
                console.print(f"[red]Unknown scenario '{scenario}'[/red]")
                console.print(f"Available: {', '.join(SHOCK_SCENARIOS.keys())}")
                raise typer.Exit(1)
            sc = SHOCK_SCENARIOS[scenario]
            console.print(
                f"\n[bold magenta]Scenario: {sc['name']}[/bold magenta]\n"
                f"  {sc['description']}\n"
                f"  Period: {sc['start_date']} → {sc['end_date']}\n"
                f"  Tickers: {', '.join(sc['tickers'][:10])}{'...' if len(sc['tickers']) > 10 else ''}\n"
                f"  Indices: {', '.join(sc.get('indices', []))}\n"
            )
            result = await run_scenario_backtest(
                scenario,
                initial_equity=equity,
                stop_loss_pct=stop_loss,
                take_profit_pct=take_profit,
            )
        else:
            from backtesting.engine import run_backtest

            if universe != "auto":
                from data_primitives.extractors.universe_builder import GLOBAL_EQUITY_SCREENS
                presets = {
                    "us_mega": GLOBAL_EQUITY_SCREENS.get("US_MEGA_CAP", []),
                    "us_large": GLOBAL_EQUITY_SCREENS.get("US_LARGE_CAP", []),
                    "europe": GLOBAL_EQUITY_SCREENS.get("EUROPE", []),
                    "asia": GLOBAL_EQUITY_SCREENS.get("ASIA", []),
                    "emerging": GLOBAL_EQUITY_SCREENS.get("EMERGING", []),
                    "supply_chain": GLOBAL_EQUITY_SCREENS.get("SUPPLY_CHAIN_CRITICAL", []),
                    "etfs": GLOBAL_EQUITY_SCREENS.get("MAJOR_ETFS", []),
                    "global": sorted(set(
                        t for region in GLOBAL_EQUITY_SCREENS.values() for t in region
                    )),
                }
                if universe in presets:
                    ticker_list = presets[universe]
                    console.print(f"[bold]Universe: {universe.upper()} ({len(ticker_list)} tickers)[/bold]")
                else:
                    ticker_list = [t.strip() for t in universe.split(",") if t.strip()]
                    console.print(f"[bold]Custom tickers: {len(ticker_list)}[/bold]")

            if fresh:
                console.print("\n[bold cyan]Refreshing all market data (indices + equities)...[/bold cyan]\n")

            if bt_start_date:
                console.print(
                    f"[bold]Date range: {bt_start_date} → {bt_end_date or 'today'}[/bold]\n"
                )

            console.print("[bold]Running Index F&O + Equity Investment Backtest...[/bold]\n")
            result = await run_backtest(
                tickers=ticker_list,
                days=days,
                initial_equity=equity,
                stop_loss_pct=stop_loss,
                take_profit_pct=take_profit,
                refresh=fresh,
                start_date=bt_start_date,
                end_date=bt_end_date,
            )

        fno_log = getattr(result, "fno_trade_log", [])

        if fresh:
            console.print("[bold green]Data refresh complete.[/bold green]\n")

        scenario_label = ""
        if result.metadata and result.metadata.get("scenario_name"):
            scenario_label = f" — {result.metadata['scenario_name']}"

        table = Table(title=f"Index F&O + Equity Investment Results{scenario_label}", border_style="green")
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right", style="bold cyan")

        table.add_row("Period", f"{result.start_date.date()} -> {result.end_date.date()}")
        table.add_row("Initial Equity", f"${result.initial_equity:,.0f}")
        table.add_row("Final Equity", f"${result.final_equity:,.0f}")

        ret_style = "green" if result.total_return_pct >= 0 else "red"
        table.add_row("Total Return", f"[{ret_style}]{result.total_return_pct:+.2f}%[/{ret_style}]")
        table.add_row("Annualized Return", f"[{ret_style}]{result.annualized_return_pct:+.2f}%[/{ret_style}]")
        table.add_row("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
        table.add_row("Max Drawdown", f"[red]{result.max_drawdown_pct:.1f}%[/red]")
        table.add_row("Total Trades", str(result.total_trades))
        table.add_row("Win Rate", f"{result.win_rate:.0f}%")
        table.add_row("Avg Hold Days", f"{result.avg_hold_days:.1f}")
        table.add_row("Profit Factor", f"{result.profit_factor:.2f}")

        indices_traded = result.extra_stats.get("indices_traded", [])
        index_countries = result.extra_stats.get("index_countries", {})
        idx_display = ", ".join(
            f"{idx_sym} ({index_countries.get(idx_sym, '?')})" for idx_sym in indices_traded
        ) if indices_traded else "None"
        table.add_row("Indices Traded (F&O)", idx_display[:80])
        table.add_row("Index F&O Trades", str(result.extra_stats.get("fno_trades", 0)))
        table.add_row("Equity Investments", str(result.extra_stats.get("equity_trades", 0)))
        table.add_row("Ticker-Days Scanned", str(result.shocks_analyzed))
        table.add_row("Decisions Made", str(result.signals_generated))
        table.add_row("Filtered (No Edge)", str(result.signals_filtered))

        console.print(table)

        # R-multiple distribution panel
        r_stats = result.extra_stats.get("r_distribution", {})
        if r_stats:
            r_table = Table(title="R-Multiple Distribution (Institutional Quality)", border_style="blue")
            r_table.add_column("Metric", style="bold")
            r_table.add_column("Value", justify="right", style="bold cyan")

            r_table.add_row("F&O Trades", str(result.extra_stats.get("fno_trades", 0)))
            r_table.add_row("Equity Trades", str(result.extra_stats.get("equity_trades", 0)))
            r_table.add_row("F&O PnL", f"${result.extra_stats.get('fno_pnl', 0):+,.0f}")
            r_table.add_row("", "")
            r_table.add_row("Trades Accepted (R >= min)", str(r_stats.get("trades_accepted", 0)))
            r_table.add_row("Trades Rejected (low R)", str(r_stats.get("trades_rejected_low_r", 0)))
            r_table.add_row("Avg Expected R", f"{r_stats.get('avg_expected_r', 0):.2f}")
            r_table.add_row("Avg Realized R", f"{r_stats.get('avg_realized_r', 0):.2f}")
            r_table.add_row("Median Realized R", f"{r_stats.get('median_realized_r', 0):.2f}")

            pos_r = r_stats.get("pct_positive_r", 0)
            r_style = "green" if pos_r > 50 else "red"
            r_table.add_row("% Positive R Trades", f"[{r_style}]{pos_r:.1f}%[/{r_style}]")
            r_table.add_row("R 75th Percentile", f"{r_stats.get('r_75th', 0):.2f}")
            r_table.add_row("R 25th Percentile", f"{r_stats.get('r_25th', 0):.2f}")

            console.print(r_table)

        if result.trades:
            trade_table = Table(title=f"Equity Trade Log ({len(result.trades)} trades)", border_style="dim")
            trade_table.add_column("Ticker", style="bold")
            trade_table.add_column("Action", justify="center")
            trade_table.add_column("Entry", justify="right")
            trade_table.add_column("Exit", justify="right")
            trade_table.add_column("PnL", justify="right")
            trade_table.add_column("PnL%", justify="right")
            trade_table.add_column("Days", justify="right")
            trade_table.add_column("Strategy")

            for t in result.trades[:20]:
                pnl_style = "green" if t.pnl > 0 else "red"
                trade_table.add_row(
                    t.signal.ticker,
                    t.signal.action.value,
                    f"${t.entry_price:.2f}",
                    f"${t.exit_price:.2f}" if t.exit_price else "-",
                    f"[{pnl_style}]${t.pnl:+,.0f}[/{pnl_style}]",
                    f"[{pnl_style}]{t.pnl_pct:+.1f}%[/{pnl_style}]",
                    str(t.hold_days),
                    t.signal.rationale[:50],
                )

            console.print(trade_table)

        # ── Index F&O Trade Log ──
        if fno_log:
            fno_table = Table(title=f"Index F&O Trade Log ({len(fno_log)} trades)", border_style="magenta")
            fno_table.add_column("Contract", style="bold")
            fno_table.add_column("Cty", justify="center")
            fno_table.add_column("Dir", justify="center")
            fno_table.add_column("Lots", justify="right")
            fno_table.add_column("Entry", justify="right")
            fno_table.add_column("Exit", justify="right")
            fno_table.add_column("Expiry", justify="center")
            fno_table.add_column("DTE", justify="right")
            fno_table.add_column("PnL", justify="right")
            fno_table.add_column("R", justify="right")
            fno_table.add_column("Reason")

            for ft in fno_log[:30]:
                pnl_style = "green" if ft.pnl > 0 else "red"
                det = ft.details or {}
                realized_r = det.get("realized_r", ft.pnl / max(ft.entry_cost, 0.01))
                r_style = "green" if realized_r > 0 else "red"
                exit_reason = det.get("exit_reason", "expiry")
                contract = det.get("contract", ft.underlying)
                country = det.get("country", "")
                lots = det.get("lots", 1)
                expiry_date = det.get("expiry_date", "")
                dte = det.get("dte_at_entry", ft.hold_days)

                if ft.instrument == "INDEX_FUTURE":
                    entry_str = f"F:{det.get('fut_entry', 0):,.1f}"
                    exit_str = f"F:{det.get('fut_exit', 0):,.1f}"
                elif ft.instrument in ("INDEX_CALL", "INDEX_PUT"):
                    entry_str = f"K{det.get('strike', 0):,.0f} P:{det.get('entry_premium', 0):,.1f}"
                    exit_str = f"P:{det.get('exit_premium', 0):,.1f}"
                elif ft.instrument == "INDEX_SPREAD":
                    entry_str = f"D:{det.get('net_debit', 0):,.2f}"
                    exit_str = f"V:{det.get('exit_spread_val', 0):,.2f}"
                else:
                    entry_str = f"${ft.entry_cost:,.0f}"
                    exit_str = f"${ft.exit_value:,.0f}"

                fno_table.add_row(
                    str(contract)[:20],
                    country,
                    ft.direction[:1],
                    str(lots),
                    entry_str,
                    exit_str,
                    expiry_date[-5:] if expiry_date else "",
                    str(dte),
                    f"[{pnl_style}]${ft.pnl:+,.0f}[/{pnl_style}]",
                    f"[{r_style}]{realized_r:+.2f}R[/{r_style}]",
                    exit_reason[:12],
                )
            console.print(fno_table)
        else:
            console.print("[dim]No index F&O trades executed.[/dim]")

        from backtesting.report_generator import generate_backtest_report
        paths = generate_backtest_report(result, output_dir=Path("."))
        console.print(f"\n[dim]→ Wrote {paths['json']}[/dim]")
        console.print(f"[dim]→ Wrote {paths['txt']}[/dim]\n")

    asyncio.run(_run())


@data_app.command("bulk-ingest")
def bulk_ingest(
    start: str = typer.Option("2018-01-01", help="History start date (ISO)"),
    end: str = typer.Option(None, help="History end date (ISO, defaults to today)"),
    equities: bool = typer.Option(True, help="Ingest equity tickers"),
    indices: bool = typer.Option(True, help="Ingest tradeable indices"),
    extra: list[str] = typer.Option(None, help="Extra equity tickers to add"),
):
    """One-shot historical bulk ingest for walk-forward backtests.

    Pulls daily OHLCV from start → end for every cached equity ticker
    and every tradeable index, writing them into ``data_store/ohlcv/``
    and ``data_store/indices/``. Walk-forward then runs purely off cache
    (no per-window Yahoo fetches) and finishes in minutes instead of
    hours.

    Example:
        ayvid data bulk-ingest --start 2018-01-01
    """
    async def _run():
        import datetime as dt
        from backtesting.engine import (
            _DATA_DIR, _save_equity_bars, _save_index_bars,
        )
        from data_primitives.extractors.openbb_client import (
            fetch_ohlcv, fetch_index_historical,
        )
        from graph_intelligence.agents.strategy_orchestrator import (
            TRADEABLE_INDEX_SYMBOLS,
        )

        end_iso = end or dt.date.today().isoformat()

        equity_tickers: list[str] = []
        if equities:
            cached = sorted(p.stem for p in _DATA_DIR.glob("*.jsonl"))
            equity_tickers = sorted(set(cached + list(extra or [])))

        index_tickers: list[str] = list(TRADEABLE_INDEX_SYMBOLS) if indices else []

        console.print(
            f"\n[bold]Bulk historical ingest[/bold]\n"
            f"  Span: {start} → {end_iso}\n"
            f"  Equities: {len(equity_tickers)} tickers\n"
            f"  Indices:  {len(index_tickers)} symbols\n"
        )

        eq_ok = eq_skip = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            if equity_tickers:
                t = progress.add_task("Equities", total=len(equity_tickers))
                for sym in equity_tickers:
                    try:
                        bars = await fetch_ohlcv(sym, start_date=start, end_date=end_iso)
                        if bars:
                            _save_equity_bars(sym, bars)
                            eq_ok += 1
                        else:
                            eq_skip += 1
                    except Exception as exc:
                        eq_skip += 1
                        console.print(f"  [dim]skip {sym}: {exc}[/dim]")
                    progress.advance(t)

            ix_ok = ix_skip = 0
            if index_tickers:
                t = progress.add_task("Indices", total=len(index_tickers))
                for sym in index_tickers:
                    try:
                        bars = await fetch_index_historical(
                            sym, start_date=start, end_date=end_iso,
                        )
                        if bars:
                            _save_index_bars(sym, bars)
                            ix_ok += 1
                        else:
                            ix_skip += 1
                    except Exception as exc:
                        ix_skip += 1
                        console.print(f"  [dim]skip {sym}: {exc}[/dim]")
                    progress.advance(t)

        console.print(
            f"\n[bold green]Done.[/bold green] "
            f"equities {eq_ok} ok / {eq_skip} skipped, "
            f"indices {ix_ok} ok / {ix_skip} skipped."
        )

    asyncio.run(_run())


@data_app.command("walkforward")
def walkforward(
    start: str = typer.Option("2018-01-01", help="Walk-forward start date (ISO)"),
    end: str = typer.Option(None, help="Walk-forward end date (ISO, defaults to today)"),
    window_months: int = typer.Option(6, help="Length of each rolling window (months)"),
    step_months: int = typer.Option(3, help="Step between windows (months)"),
    equity: float = typer.Option(1_000_000.0, help="Initial equity per window ($)"),
    output_dir: str = typer.Option(
        "document/backtests",
        help="Where to write walkforward_report.{json,md}",
    ),
    min_conviction: float = typer.Option(
        0.55, help="Minimum conviction threshold for accepting signals"
    ),
    name: str = typer.Option(
        "walkforward_report", help="Output filename stem (e.g. 'walkforward_strict')"
    ),
):
    """Out-of-sample walk-forward across the entire historical span.

    Sweeps a rolling window across the chosen span and runs a fresh
    backtest on each window. No cherry-picked shocks. Reports the full
    distribution of returns, Sharpes, drawdowns, and a compounded
    aggregate equity curve. Writes ``{name}.json`` and ``{name}.md``
    in the chosen ``--output-dir`` (default ``document/backtests/``).

    Note: this is the "naive" mode — it calls the orchestrator on every
    cached symbol every trading day with no shock context. For AYVID's
    actual operating mode (selective, event-driven, graph-aware ticker
    picking), use ``ayvid data shock-walkforward``.

    Example:
        ayvid data walkforward --start 2018-01-01 --window-months 6 --step-months 3
    """
    async def _run():
        from pathlib import Path
        from backtesting.walkforward import run_walk_forward

        console.print(
            f"\n[bold]Walk-forward backtest[/bold]\n"
            f"  Span: {start} → {end or 'today'}\n"
            f"  Window: {window_months}m, step: {step_months}m\n"
            f"  Equity per window: ${equity:,.0f}\n"
            f"  Min conviction: {min_conviction:.2f}\n"
            f"  Report stem: {name}\n"
        )
        summary = await run_walk_forward(
            start=start,
            end=end,
            window_months=window_months,
            step_months=step_months,
            initial_equity=equity,
            output_dir=Path(output_dir),
            min_conviction=min_conviction,
            report_filename=name,
        )

        table = Table(
            title=f"Walk-Forward Results ({summary.n_valid_windows} valid windows)",
            border_style="green",
        )
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right", style="bold cyan")
        table.add_row("Span", f"{summary.start_date} → {summary.end_date}")
        table.add_row("Avg return / window", f"{summary.avg_return_pct:+.2f}%")
        table.add_row("Median return / window", f"{summary.median_return_pct:+.2f}%")
        table.add_row("% positive windows", f"{summary.pct_positive_windows:.1f}%")
        table.add_row("Avg Sharpe", f"{summary.avg_sharpe:.2f}")
        table.add_row("Median Sharpe", f"{summary.median_sharpe:.2f}")
        table.add_row("% Sharpe > 1.0", f"{summary.sharpe_above_one_pct:.1f}%")
        table.add_row("Worst window drawdown", f"{summary.worst_drawdown_pct:.2f}%")
        table.add_row("Pooled trades", f"{summary.pooled_total_trades:,}")
        table.add_row("Pooled win rate", f"{summary.pooled_win_rate:.1f}%")
        table.add_row("Compounded return", f"{summary.aggregate_compounded_return_pct:+.2f}%")
        table.add_row("CAGR (chained)", f"{summary.aggregate_cagr_pct:+.2f}%")
        console.print(table)
        console.print(f"\n[dim]→ wrote {output_dir}/{name}.json[/dim]")
        console.print(f"[dim]→ wrote {output_dir}/{name}.md[/dim]\n")

    asyncio.run(_run())


@data_app.command("shock-walkforward")
def shock_walkforward(
    start: str = typer.Option("2018-01-01", help="Span start date (ISO)"),
    end: str = typer.Option(None, help="Span end date (ISO, defaults to today)"),
    equity: float = typer.Option(1_000_000.0, help="Initial equity per scenario ($)"),
    output_dir: str = typer.Option(
        "document/backtests", help="Where to write shock_driven_report.{json,md}"
    ),
    min_conviction: float = typer.Option(
        0.55, help="Minimum conviction threshold for accepting signals"
    ),
    name: str = typer.Option(
        "shock_driven_report", help="Output filename stem"
    ),
    only: str = typer.Option(
        None, help="Comma-separated scenario IDs to restrict to (e.g. 'suez_canal_2021,covid_crash_2020')"
    ),
):
    """AYVID's actual operating mode — selective, event-driven backtest.

    Iterates the named historical shock catalog (20 events, 2018-2025)
    and runs each one through ``run_scenario_backtest`` so the orchestrator
    receives proper shock context (beneficiary/victim sets, index bias,
    causal chain). Only causally-linked tickers trade in each window.

    This is the *honest* sibling of ``walkforward``: instead of trading
    every ticker every day on technicals, it trades only when the graph
    layer would have detected a real shock, and only on the tickers the
    graph would have surfaced.

    Example:
        ayvid data shock-walkforward --start 2018-01-01 --end 2025-12-31
    """
    async def _run():
        from pathlib import Path
        from backtesting.walkforward import run_shock_driven_walkforward

        only_list = [s.strip() for s in only.split(",")] if only else None

        console.print(
            f"\n[bold]Shock-driven walk-forward backtest[/bold]\n"
            f"  Span: {start} → {end or 'today'}\n"
            f"  Equity per scenario: ${equity:,.0f}\n"
            f"  Min conviction: {min_conviction:.2f}\n"
            f"  Report stem: {name}\n"
            + (f"  Restricting to: {', '.join(only_list)}\n" if only_list else "")
        )
        summary = await run_shock_driven_walkforward(
            start=start,
            end=end,
            initial_equity=equity,
            min_conviction=min_conviction,
            output_dir=Path(output_dir),
            report_filename=name,
            only=only_list,
        )

        table = Table(
            title=f"Shock-Driven Results ({summary.n_valid}/{summary.n_run} valid scenarios)",
            border_style="green",
        )
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right", style="bold cyan")
        table.add_row("Span", f"{summary.span_start} → {summary.span_end}")
        table.add_row("Catalog size", f"{summary.catalog_size}")
        table.add_row("Avg trades / scenario", f"{summary.avg_trades_per_scenario:.1f}")
        table.add_row("Pooled trades", f"{summary.pooled_total_trades:,}")
        table.add_row("Avg return / scenario", f"{summary.avg_return_pct:+.2f}%")
        table.add_row("Median return / scenario", f"{summary.median_return_pct:+.2f}%")
        table.add_row("% positive scenarios", f"{summary.pct_positive_scenarios:.1f}%")
        table.add_row("Avg Sharpe", f"{summary.avg_sharpe:.2f}")
        table.add_row("Median Sharpe", f"{summary.median_sharpe:.2f}")
        table.add_row("% Sharpe > 1.0", f"{summary.sharpe_above_one_pct:.1f}%")
        table.add_row("Worst scenario DD", f"{summary.worst_drawdown_pct:.2f}%")
        table.add_row("Pooled win rate", f"{summary.pooled_win_rate:.1f}%")
        table.add_row("Compounded return", f"{summary.aggregate_compounded_return_pct:+.2f}%")
        table.add_row("CAGR (chained)", f"{summary.aggregate_cagr_pct:+.2f}%")
        console.print(table)
        console.print(f"\n[dim]→ wrote {output_dir}/{name}.json[/dim]")
        console.print(f"[dim]→ wrote {output_dir}/{name}.md[/dim]\n")

    asyncio.run(_run())


@data_app.command("backtest-fno", deprecated=True)
def backtest_fno(
    days: int = typer.Option(60, help="Number of days to backtest"),
):
    """[Deprecated] Use 'backtest' instead — it now includes F&O strategies automatically."""
    console.print("[yellow]Note: backtest-fno is deprecated. The unified 'backtest' command now handles F&O.[/yellow]")
    backtest(days=days)


@data_app.command("report")
def report(
    scenarios: str = typer.Option(
        None, "--scenarios",
        help="Comma-separated scenario keys or 'all' to run every named shock scenario.",
    ),
    compare_baseline: bool = typer.Option(
        False, "--compare-baseline",
        help="Run a baseline backtest (no causal intelligence) alongside for alpha delta analysis.",
    ),
    equity: float = typer.Option(1_000_000.0, help="Initial equity ($)"),
):
    """Generate a YC-ready performance report.

    Runs targeted backtests for historical shock scenarios and produces
    structured reports in JSON and Markdown for the YC application.

    Examples:
        ayvid data report --scenarios all
        ayvid data report --scenarios suez_canal_2021,semiconductor_2022
        ayvid data report --scenarios all --compare-baseline
    """
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = min(hard, 4096)
    if soft < target:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except (ValueError, OSError):
            pass

    async def _run():
        import gc
        import sqlite3
        from pathlib import Path
        from backtesting.engine import SHOCK_SCENARIOS, run_scenario_backtest, run_backtest
        from backtesting.report_generator import generate_yc_report

        if scenarios == "all":
            scenario_keys = list(SHOCK_SCENARIOS.keys())
        elif scenarios:
            scenario_keys = [s.strip() for s in scenarios.split(",") if s.strip()]
        else:
            scenario_keys = list(SHOCK_SCENARIOS.keys())

        invalid = [k for k in scenario_keys if k not in SHOCK_SCENARIOS]
        if invalid:
            console.print(f"[red]Unknown scenarios: {', '.join(invalid)}[/red]")
            console.print(f"Available: {', '.join(SHOCK_SCENARIOS.keys())}")
            raise typer.Exit(1)

        console.print(
            f"\n[bold]Running {len(scenario_keys)} scenario backtests for YC report...[/bold]\n"
        )

        ai_results = []
        baseline_results = [] if compare_baseline else None

        for i, key in enumerate(scenario_keys, 1):
            sc = SHOCK_SCENARIOS[key]
            console.print(
                f"  [{i}/{len(scenario_keys)}] [bold magenta]{sc['name']}[/bold magenta] "
                f"({sc['start_date']} → {sc['end_date']})"
            )

            try:
                result = await run_scenario_backtest(key, initial_equity=equity)
                ai_results.append(result)
                console.print(
                    f"    → Return: {result.total_return_pct:+.2f}% | "
                    f"Sharpe: {result.sharpe_ratio:.2f} | "
                    f"Trades: {result.total_trades}"
                )
            except Exception as exc:
                console.print(f"    [red]→ Failed: {exc}[/red]")
                gc.collect()
                continue

            if compare_baseline:
                console.print(f"    Running baseline (naive quant, no causal intelligence)...")
                try:
                    base = await run_scenario_backtest(
                        key,
                        initial_equity=equity,
                        use_baseline=True,
                    )
                    baseline_results.append(base)
                    console.print(
                        f"    → Baseline: {base.total_return_pct:+.2f}% | "
                        f"Sharpe: {base.sharpe_ratio:.2f}"
                    )
                except Exception as exc:
                    console.print(f"    [red]→ Baseline failed: {exc}[/red]")
                    if baseline_results is not None:
                        baseline_results.append(ai_results[-1])

            gc.collect()

        if not ai_results:
            console.print("[red]No scenarios completed successfully.[/red]")
            raise typer.Exit(1)

        console.print("\n[bold]Computing buy-and-hold benchmarks...[/bold]")
        from backtesting.report_generator import compute_buy_and_hold
        from data_primitives.extractors.openbb_client import fetch_multi_ohlcv

        buy_hold_data: list[dict] = []
        for i, key in enumerate(scenario_keys):
            if i >= len(ai_results):
                break
            sc = SHOCK_SCENARIOS[key]
            try:
                bh_bars = await fetch_multi_ohlcv(
                    sc["tickers"][:20],
                    start_date=sc["start_date"],
                    end_date=sc["end_date"],
                )
                bh = compute_buy_and_hold(bh_bars, sc["start_date"], sc["end_date"])
                buy_hold_data.append(bh)
                console.print(
                    f"  {sc['name']}: Buy & Hold = {bh['portfolio_return_pct']:+.2f}% "
                    f"(avg DD {bh['avg_max_drawdown_pct']:.1f}%, {bh['count']} tickers)"
                )
            except Exception as exc:
                console.print(f"  [red]{sc['name']}: B&H failed: {exc}[/red]")
                buy_hold_data.append({"portfolio_return_pct": 0.0, "count": 0})
            gc.collect()

        console.print("\n[bold]Generating YC performance report...[/bold]")
        output_files = generate_yc_report(
            results=ai_results,
            baseline_results=baseline_results,
            buy_hold_benchmarks=buy_hold_data or None,
            output_dir=Path("."),
        )

        console.print(f"\n[bold green]Report generated:[/bold green]")
        for fmt, path in output_files.items():
            console.print(f"  → {fmt}: {path}")
        console.print("")

    asyncio.run(_run())
