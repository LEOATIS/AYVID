"""Supply chain graph intelligence CLI commands."""
from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import SpinnerColumn, Progress, TextColumn

console = Console()
graph_app = typer.Typer(help="Supply chain graph intelligence", no_args_is_help=True)

_GLOBAL_SUPPLY_CHAIN = [
    # ── Semiconductor Fabrication (the beating heart) ─────────
    ("TSM", "AAPL", 0.25, 0.95),
    ("TSM", "NVDA", 0.18, 0.90),
    ("TSM", "AMD", 0.15, 0.85),
    ("TSM", "QCOM", 0.12, 0.80),
    ("TSM", "INTC", 0.08, 0.60),
    ("TSM", "AVGO", 0.10, 0.75),
    ("TSM", "MRVL", 0.06, 0.65),
    ("TSM", "MEDIATEK", 0.14, 0.85),
    ("005930.KS", "AAPL", 0.15, 0.70),
    ("005930.KS", "XIAOMI", 0.20, 0.80),
    ("005930.KS", "VERIZON", 0.10, 0.50),
    ("005930.KS", "T", 0.08, 0.45),
    ("UMC", "QCOM", 0.08, 0.50),
    ("GFS", "AMD", 0.10, 0.55),
    ("GFS", "QCOM", 0.12, 0.60),
    # ── Semiconductor Equipment (bottleneck layer) ────────────
    ("ASML", "TSM", 0.30, 0.99),
    ("ASML", "005930.KS", 0.20, 0.95),
    ("ASML", "INTC", 0.15, 0.90),
    ("ASML", "GFS", 0.05, 0.80),
    ("AMAT", "TSM", 0.20, 0.85),
    ("AMAT", "005930.KS", 0.15, 0.80),
    ("LRCX", "TSM", 0.18, 0.85),
    ("LRCX", "INTC", 0.12, 0.75),
    ("KLAC", "TSM", 0.15, 0.80),
    ("ENTG", "TSM", 0.10, 0.70),
    ("TER", "NVDA", 0.08, 0.60),
    # ── Consumer Electronics ──────────────────────────────────
    ("AAPL", "2317.TW", 0.60, 0.95),
    ("AAPL", "FLEX", 0.05, 0.40),
    ("DELL", "INTC", 0.25, 0.70),
    ("DELL", "AMZN", 0.08, 0.40),
    ("HPQ", "INTC", 0.20, 0.65),
    ("LNVGY", "INTC", 0.15, 0.60),
    ("LNVGY", "AMD", 0.08, 0.45),
    ("SONY", "TSM", 0.12, 0.70),
    ("SONY", "AMD", 0.15, 0.65),
    # ── Cloud / AI Infrastructure ─────────────────────────────
    ("NVDA", "MSFT", 0.20, 0.80),
    ("NVDA", "META", 0.15, 0.75),
    ("NVDA", "GOOG", 0.15, 0.75),
    ("NVDA", "AMZN", 0.12, 0.70),
    ("NVDA", "TSLA", 0.08, 0.55),
    ("NVDA", "ORCL", 0.06, 0.50),
    ("AMD", "MSFT", 0.10, 0.55),
    ("AMD", "GOOG", 0.06, 0.45),
    ("AVGO", "AAPL", 0.20, 0.80),
    ("AVGO", "GOOG", 0.08, 0.50),
    ("QCOM", "005930.KS", 0.20, 0.75),
    ("QCOM", "XIAOMI", 0.15, 0.65),
    # ── Enterprise Software (downstream) ──────────────────────
    ("MSFT", "CRM", 0.06, 0.35),
    ("MSFT", "NOW", 0.04, 0.30),
    ("AMZN", "SNOW", 0.05, 0.40),
    ("GOOG", "DDOG", 0.04, 0.30),
    ("ORCL", "SAP", 0.03, 0.25),
    # ── Automotive Supply Chain ───────────────────────────────
    ("TSLA", "PANASONIC", 0.30, 0.90),
    ("TSLA", "CATL", 0.25, 0.85),
    ("TSLA", "NXPI", 0.08, 0.60),
    ("TM", "DENSO", 0.15, 0.80),
    ("TM", "AISIN", 0.10, 0.70),
    ("BMW", "CATL", 0.12, 0.75),
    ("BMW", "CONTINENTAL", 0.10, 0.65),
    ("VW", "BOSCH", 0.15, 0.80),
    ("VW", "CONTINENTAL", 0.12, 0.70),
    ("GM", "LG_ENERGY", 0.20, 0.85),
    ("F", "SK_INNOVATION", 0.15, 0.75),
    ("RIVN", "005930.KS", 0.10, 0.60),
    # ── Energy & Commodities ──────────────────────────────────
    ("XOM", "CVX", 0.05, 0.20),
    ("SHEL", "BP", 0.04, 0.20),
    ("SHEL", "TOTALENERGIES", 0.05, 0.25),
    ("RIO", "BHP", 0.06, 0.25),
    ("RIO", "VALE", 0.08, 0.30),
    ("BHP", "CATERPILLAR", 0.10, 0.50),
    ("VALE", "TSLA", 0.05, 0.40),
    ("FREEPORT", "TSLA", 0.04, 0.35),
    ("ALBEMARLE", "TSLA", 0.08, 0.60),
    ("ALBEMARLE", "CATL", 0.12, 0.70),
    # ── Global Logistics & Shipping ───────────────────────────
    ("AMKBY", "AAPL", 0.03, 0.50),
    ("AMKBY", "005930.KS", 0.03, 0.45),
    ("AMKBY", "WALMART", 0.04, 0.55),
    ("FDX", "AMZN", 0.15, 0.60),
    ("UPS", "AMZN", 0.12, 0.55),
    ("XPO", "WALMART", 0.08, 0.45),
    ("ZIM", "COSTCO", 0.05, 0.40),
    # ── Pharmaceuticals & Biotech ─────────────────────────────
    ("ROCHE", "GENENTECH", 0.30, 0.90),
    ("NOVARTIS", "SANDOZ", 0.25, 0.85),
    ("JNJ", "JANSSEN", 0.35, 0.90),
    ("PFE", "BNTX", 0.15, 0.70),
    ("AZN", "ALEXION", 0.20, 0.75),
    ("MRK", "ORGANON", 0.10, 0.50),
    ("LLY", "AAPL", 0.00, 0.00),
    ("ABBV", "ROCHE", 0.03, 0.20),
    # ── Financials ────────────────────────────────────────────
    ("JPM", "V", 0.05, 0.30),
    ("JPM", "MA", 0.04, 0.25),
    ("GS", "MSFT", 0.03, 0.20),
    ("HSBC", "SHEL", 0.04, 0.25),
    ("HSBC", "BHP", 0.03, 0.20),
    # ── Telecom ───────────────────────────────────────────────
    ("ERICSSON", "T", 0.12, 0.60),
    ("ERICSSON", "VERIZON", 0.10, 0.55),
    ("NOKIA", "T", 0.08, 0.50),
    ("NOKIA", "DT", 0.10, 0.55),
    # ── Defense & Aerospace ───────────────────────────────────
    ("RTX", "BA", 0.15, 0.70),
    ("RTX", "LMT", 0.10, 0.60),
    ("GE", "BA", 0.20, 0.80),
    ("HON", "BA", 0.10, 0.55),
    ("HON", "AIRBUS", 0.08, 0.50),
    ("LMT", "US_DOD", 0.60, 0.95),
    ("NOC", "US_DOD", 0.55, 0.90),
    # ── Retail & Consumer ─────────────────────────────────────
    ("WALMART", "PG", 0.08, 0.40),
    ("WALMART", "NESTLE", 0.05, 0.30),
    ("AMZN", "SHOPIFY", 0.04, 0.30),
    ("COSTCO", "PG", 0.06, 0.35),
]


@graph_app.command("init")
def init_graph():
    """Initialize Neo4j schema with constraints and indexes."""
    async def _run():
        try:
            from graph_intelligence.tools.neo4j_tools import initialize_schema
            await initialize_schema()
            console.print("[bold green]Neo4j schema initialized successfully.[/bold green]")
        except Exception as e:
            console.print(f"[bold red]Schema init failed:[/bold red] {e}")
            console.print("[dim]Make sure Neo4j is running and NEO4J_PASSWORD is set in .env[/dim]")

    asyncio.run(_run())


@graph_app.command("seed")
def seed_graph():
    """Seed the global supply chain graph (semiconductors, auto, energy, pharma, logistics, defense)."""
    async def _run():
        from shared.types import SupplyEdge

        valid = [(s, t, d, c) for s, t, d, c in _GLOBAL_SUPPLY_CHAIN if d > 0]
        console.print(f"\n[bold]Seeding global supply chain: {len(valid)} edges across 10+ sectors[/bold]\n")

        try:
            from graph_intelligence.tools.neo4j_tools import bulk_ingest_supply_chain
            edges = [
                SupplyEdge(
                    source_ticker=src,
                    target_ticker=tgt,
                    revenue_dependency_pct=dep,
                    criticality_score=crit,
                )
                for src, tgt, dep, crit in valid
            ]
            count = await bulk_ingest_supply_chain(edges)

            unique_companies = set()
            for s, t, _, _ in valid:
                unique_companies.add(s)
                unique_companies.add(t)

            sectors = {
                "Semiconductors", "Equipment", "Consumer Electronics", "Cloud/AI",
                "Automotive", "Energy", "Logistics", "Pharma", "Financials",
                "Telecom", "Defense", "Retail",
            }

            console.print(f"[bold green]Seeded {count} supply chain edges[/bold green]")
            console.print(f"  Companies: [bold]{len(unique_companies)}[/bold]")
            console.print(f"  Sectors:   [bold]{len(sectors)}[/bold] ({', '.join(sorted(sectors))})")
        except Exception as e:
            console.print(f"[bold red]Seeding failed:[/bold red] {e}")
            console.print("[dim]Make sure Neo4j is running and NEO4J_PASSWORD is set in .env[/dim]")

    asyncio.run(_run())


@graph_app.command("stats")
def graph_stats():
    """Show supply chain graph statistics."""
    async def _run():
        try:
            from graph_intelligence.tools.neo4j_tools import get_graph_stats
            stats = await get_graph_stats()
            console.print(Panel(
                f"  Companies: [bold]{stats['companies']}[/bold]\n"
                f"  Edges:     [bold]{stats['edges']}[/bold]",
                title="[bold]Supply Chain Graph[/bold]",
                border_style="cyan",
            ))
        except Exception as e:
            console.print(f"[red]Cannot reach Neo4j:[/red] {e}")

    asyncio.run(_run())


@graph_app.command("scan")
def scan_shock(
    ticker: str = typer.Argument(help="Shock ticker (e.g., TSM)"),
    depth: int = typer.Option(4, help="Max traversal depth"),
    min_risk: float = typer.Option(0.01, help="Minimum compound risk threshold"),
):
    """Scan for N-th order hidden victims of a supply chain shock."""
    async def _run():
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(f"Scanning topology from {ticker}...", total=None)
            try:
                from graph_intelligence.tools.neo4j_tools import find_hidden_victims
                paths = await find_hidden_victims(
                    ticker, max_depth=depth, min_compound_risk=min_risk,
                )
                from cli.widgets.supply_chain import render_causal_paths, render_supply_chain_ascii
                console.print(render_supply_chain_ascii(ticker, paths))
                console.print(render_causal_paths(ticker, paths))
            except Exception as e:
                console.print(f"[red]Scan failed:[/red] {e}")

    asyncio.run(_run())


@graph_app.command("analyze")
def analyze_shock(
    ticker: str = typer.Argument(help="Shock ticker to analyze"),
):
    """Run full LangGraph causal analysis pipeline for a shock event."""
    async def _run():
        console.print(f"\n[bold]Running N-th Order Causal Analysis for [cyan]{ticker}[/cyan][/bold]\n")

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching news timeline...", total=None)

            from data_primitives.extractors.news_scraper import (
                scrape_news_for_tickers, news_to_causal_events,
            )
            news = await scrape_news_for_tickers([ticker])
            events = news_to_causal_events(news)
            progress.update(task, description=f"Got {len(events)} events. Running LangGraph pipeline...")

            from graph_intelligence.pipeline import run_causal_analysis
            signal = await run_causal_analysis(
                shock_ticker=ticker,
                timeline=events,
            )

        if signal:
            from cli.widgets.signals import render_signal_detail
            console.print(render_signal_detail(signal))
        else:
            console.print("[yellow]No actionable trade signal produced.[/yellow]")

    asyncio.run(_run())


@graph_app.command("centrality")
def compute_centrality():
    """Compute PageRank and Betweenness Centrality for the graph."""
    async def _run():
        try:
            from graph_intelligence.tools.neo4j_tools import compute_pagerank, compute_betweenness

            console.print("[bold]Computing centrality metrics...[/bold]")

            pr = await compute_pagerank()
            bc = await compute_betweenness()

            table = Table(title="Node Centrality (Top 15)", border_style="magenta")
            table.add_column("Ticker", style="bold")
            table.add_column("PageRank", justify="right")
            table.add_column("Betweenness", justify="right")
            table.add_column("Type", justify="center")

            pr_map = {r["ticker"]: r["score"] for r in pr}
            bc_map = {r["ticker"]: r["score"] for r in bc}
            all_tickers = sorted(set(list(pr_map.keys()) + list(bc_map.keys())))

            rows = []
            for t in all_tickers:
                rows.append((t, pr_map.get(t, 0), bc_map.get(t, 0)))
            rows.sort(key=lambda x: x[1], reverse=True)

            for t, pr_s, bc_s in rows[:15]:
                node_type = "[red]UBIQUITOUS[/red]" if pr_s > 0.85 else (
                    "[cyan]BOTTLENECK[/cyan]" if bc_s > 5 else "[dim]STANDARD[/dim]"
                )
                table.add_row(t, f"{pr_s:.4f}", f"{bc_s:.2f}", node_type)

            console.print(table)
        except Exception as e:
            console.print(f"[red]Centrality computation failed:[/red] {e}")

    asyncio.run(_run())


@graph_app.command("refine")
def refine_causal_graph(
    iterations: int = typer.Option(
        5, help="Max LLM↔falsification iterations (α)",
    ),
    gamma: float = typer.Option(
        0.80, help="Minimum LLM self-confidence to apply refinement (γ)",
    ),
    returns_days: int = typer.Option(
        252, help="Days of return history used for CI tests",
    ),
    dry_run: bool = typer.Option(
        False, help="Compute + report, but do NOT write confidence to Neo4j",
    ),
    edge_limit: int = typer.Option(
        200, help="Maximum edges pulled from Neo4j for this sweep",
    ),
):
    """Phase-2 CausalFusion refinery — validate supply-chain DAG against returns.

    Pulls the current SUPPLIES_TO edges from Neo4j, runs them through
    an α-iteration LLM↔graph-falsification loop (CI tests, causal
    minimality, random-graph MEC benchmark), and writes the resulting
    per-edge statistical ``confidence`` back to Neo4j for downstream
    ``find_hidden_victims`` filtering.

    Cadence: weekly cron is the expected deployment. This is NEVER in
    the hot decision path.

    Example:
        ayvid graph refine --iterations 5 --gamma 0.80
        ayvid graph refine --dry-run    # report without mutating Neo4j
    """
    import numpy as np
    from pathlib import Path

    async def _run():
        from neo4j.exceptions import Neo4jError
        from graph_intelligence.tools.neo4j_tools import (
            get_driver, _session_kwargs, bulk_update_edge_confidences,
        )
        from graph_intelligence.causal_falsification import (
            EdgeProposal, refine_dag,
        )
        from backtesting.engine import _load_local_bars

        # 1. Pull edges from Neo4j.
        try:
            driver = await get_driver()
        except Exception as exc:
            console.print(f"[red]Neo4j unavailable:[/red] {exc}")
            return
        try:
            async with driver.session(**_session_kwargs()) as session:
                result = await session.run(
                    """
                    MATCH (a:Company)-[r:SUPPLIES_TO]->(b:Company)
                    RETURN a.ticker AS src, b.ticker AS dst,
                           COALESCE(r.confidence, 0.5) AS conf
                    LIMIT $n
                    """,
                    n=edge_limit,
                )
                edges: list[EdgeProposal] = []
                tickers: set[str] = set()
                async for record in result:
                    s = record["src"]; d = record["dst"]; c = record["conf"]
                    edges.append(EdgeProposal(src=s, dst=d, confidence=float(c)))
                    tickers.add(s); tickers.add(d)
        except Exception as exc:
            console.print(f"[red]Edge fetch failed:[/red] {exc}")
            return

        if not edges:
            console.print("[yellow]No SUPPLIES_TO edges found in Neo4j[/yellow]")
            return
        console.print(f"Loaded [bold]{len(edges)}[/bold] edges over "
                      f"{len(tickers)} tickers")

        # 2. Load return data from data_store/.
        returns: dict[str, np.ndarray] = {}
        for t in tickers:
            bars = _load_local_bars(t)
            if len(bars) < 60:
                continue
            closes = np.array([b.close for b in bars[-returns_days:]])
            log_ret = np.diff(np.log(closes))
            returns[t] = log_ret

        console.print(f"Return data available for [bold]{len(returns)}[/bold]"
                      f"/{len(tickers)} tickers")
        if len(returns) < 3:
            console.print("[red]Insufficient return data (< 3 tickers)[/red]")
            return

        # 3. Run refinery.
        console.print(f"\n[bold]Running refinery[/bold] — α={iterations}, γ={gamma}")
        refined = await refine_dag(
            edges, returns, alpha=iterations, gamma=gamma,
        )

        console.print(
            f"\n[bold]Refinery complete[/bold]: "
            f"converged={refined.converged}, "
            f"iterations={refined.iterations_run}, "
            f"edges_out={len(refined.edges)}"
        )
        if refined.last_report is not None:
            r = refined.last_report
            console.print(
                f"  CI violations: {len(r.ci_violations)} "
                f"({r.ci_fraction_violated:.1%})\n"
                f"  Unnecessary edges: {len(r.unnecessary_edges)}\n"
                f"  Random-benchmark p: {r.random_benchmark_p:.3f}\n"
                f"  Falsifiable: {r.falsifiable}  "
                f"Falsified: {r.falsified_by_benchmark}"
            )
        for line in refined.rationale_trail:
            console.print(f"  • {line}")

        # 4. Persist.
        if dry_run:
            console.print("[dim]--dry-run: skipping Neo4j write[/dim]")
            return

        updates = [(e.src, e.dst, e.confidence) for e in refined.edges]
        written = await bulk_update_edge_confidences(updates)
        console.print(f"[green]✓ Wrote {written} edge confidences to Neo4j[/green]")

        # 5. Snapshot the refined DAG to disk for audit.
        out = Path("calibration/refined_dag.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(__import__("json").dumps({
            "iterations_run": refined.iterations_run,
            "converged": refined.converged,
            "edges": [e.model_dump() for e in refined.edges],
            "rationale_trail": refined.rationale_trail,
        }, indent=2))
        console.print(f"  → snapshot: {out}")

    asyncio.run(_run())
