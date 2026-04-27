"""System status & configuration CLI commands."""

from __future__ import annotations

import asyncio
import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
system_app = typer.Typer(help="System status & configuration", no_args_is_help=True)


@system_app.command("status")
def status():
    """Show system status and service connectivity."""

    async def _run():
        from shared.config import settings

        checks: list[tuple[str, bool, str]] = []

        # Python version
        checks.append(("Python", True, sys.version.split()[0]))

        # LLM Provider
        if settings.LLM_PROVIDER == "openai" and settings.OPENAI_API_KEY:
            checks.append(("OpenAI API", True, f"Model: {settings.OPENAI_MODEL}"))
        elif settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
            checks.append(("Anthropic API", True, f"Model: {settings.ANTHROPIC_MODEL}"))
        else:
            checks.append(("LLM Provider", False, f"No {settings.LLM_PROVIDER} API key"))

        # Secondary LLM
        if settings.ANTHROPIC_API_KEY and settings.LLM_PROVIDER != "anthropic":
            checks.append(("Anthropic (backup)", True, "Available"))
        elif settings.OPENAI_API_KEY and settings.LLM_PROVIDER != "openai":
            checks.append(("OpenAI (backup)", True, "Available"))

        # Neo4j
        try:
            from graph_intelligence.tools.neo4j_tools import verify_connection

            neo4j_ok = await verify_connection()
            checks.append(
                ("Neo4j", neo4j_ok, settings.NEO4J_URI if neo4j_ok else "Connection failed")
            )
        except Exception as e:
            checks.append(("Neo4j", False, str(e)[:50]))

        # Qdrant
        try:
            from graph_intelligence.tools.qdrant_tools import verify_connection

            qdrant_ok = await verify_connection()
            checks.append(
                ("Qdrant", qdrant_ok, settings.QDRANT_URL if qdrant_ok else "Connection failed")
            )
        except Exception as e:
            checks.append(("Qdrant", False, str(e)[:50]))

        # OpenBB
        checks.append(
            (
                "OpenBB FMP",
                bool(settings.OPENBB_FMP_API_KEY),
                "Configured" if settings.OPENBB_FMP_API_KEY else "Not set",
            )
        )
        checks.append(
            (
                "OpenBB FRED",
                bool(settings.OPENBB_FRED_API_KEY),
                "Configured" if settings.OPENBB_FRED_API_KEY else "Not set",
            )
        )

        # IB
        checks.append(
            (
                "Paper Trading",
                settings.PAPER_TRADING,
                "Enabled" if settings.PAPER_TRADING else "LIVE",
            )
        )
        checks.append(
            ("IB Gateway", False, f"{settings.IB_HOST}:{settings.IB_PORT} (not connected)")
        )

        # Risk
        checks.append(("Max Drawdown", True, f"{settings.MAX_DRAWDOWN_PCT}%"))
        checks.append(("Kelly Fraction", True, f"{settings.KELLY_FRACTION} (Half-Kelly)"))

        table = Table(title="AYVID System Status", border_style="cyan")
        table.add_column("Component", style="bold")
        table.add_column("Status", justify="center")
        table.add_column("Details")

        for name, ok, detail in checks:
            status = "[green]OK[/green]" if ok else "[red]--[/red]"
            table.add_row(name, status, detail)

        console.print(table)

    asyncio.run(_run())


@system_app.command("config")
def show_config():
    """Display current configuration (secrets redacted)."""
    from shared.config import settings

    config_items = [
        ("LLM Provider", settings.LLM_PROVIDER),
        ("OpenAI Model", settings.OPENAI_MODEL),
        ("Anthropic Model", settings.ANTHROPIC_MODEL),
        (
            "OpenAI Key",
            "****" + settings.OPENAI_API_KEY[-4:]
            if settings.OPENAI_API_KEY and len(settings.OPENAI_API_KEY) > 4
            else "Not set",
        ),
        (
            "Anthropic Key",
            "****" + settings.ANTHROPIC_API_KEY[-4:]
            if settings.ANTHROPIC_API_KEY and len(settings.ANTHROPIC_API_KEY) > 4
            else "Not set",
        ),
        ("Neo4j URI", settings.NEO4J_URI),
        ("Qdrant URL", settings.QDRANT_URL),
        ("QuestDB", f"{settings.QUESTDB_HOST}:{settings.QUESTDB_PORT}"),
        ("IB Gateway", f"{settings.IB_HOST}:{settings.IB_PORT}"),
        ("Paper Trading", str(settings.PAPER_TRADING)),
        ("Max Drawdown", f"{settings.MAX_DRAWDOWN_PCT}%"),
        ("Kelly Fraction", str(settings.KELLY_FRACTION)),
        ("Max Position", f"{settings.MAX_POSITION_PCT}%"),
        ("Log Level", settings.LOG_LEVEL),
    ]

    table = Table(title="Configuration", border_style="dim")
    table.add_column("Setting", style="bold")
    table.add_column("Value")

    for name, value in config_items:
        table.add_row(name, str(value))

    console.print(table)


@system_app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind to"),
    port: int = typer.Option(8000, help="Port to bind to"),
):
    """Start the FastAPI backend server."""
    import uvicorn

    console.print(f"\n[bold]Starting AYVID API server on {host}:{port}[/bold]\n")
    uvicorn.run(
        "backend.core.main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info",
    )


@system_app.command("latency")
def latency():
    """Show pipeline node latencies and fast-path execution speed."""
    from backend.services.orchestrator import get_fast_execute_latency_ms
    from execution_engine.signal_book import get_signal_book
    from graph_intelligence.agents.analyst.node import get_swarm_latency_ms
    from graph_intelligence.pipeline import get_pipeline_latency

    pipeline = get_pipeline_latency()
    book_stats = get_signal_book().get_stats()
    fast_ms = get_fast_execute_latency_ms()
    swarm_ms = get_swarm_latency_ms()

    table = Table(
        title="Pipeline Latency (last cycle)",
        border_style="cyan",
        show_footer=True,
    )
    table.add_column("Node", style="bold", footer_style="bold")
    table.add_column("Latency", justify="right")

    nodes = pipeline.get("nodes", {})
    if nodes:
        for node_name, ms in nodes.items():
            if ms > 5000:
                style = "red"
            elif ms > 2000:
                style = "yellow"
            else:
                style = "green"
            table.add_row(node_name, f"[{style}]{ms:.0f}ms[/{style}]")
        table.columns[0].footer = "TOTAL"
        table.columns[1].footer = f"{pipeline['total_s']:.1f}s"
    else:
        table.add_row("[dim]No pipeline run yet[/dim]", "—")

    console.print(table)

    fast_table = Table(title="Execution Tiers", border_style="green")
    fast_table.add_column("Tier", style="bold")
    fast_table.add_column("Speed", justify="right")
    fast_table.add_column("Status")

    # Tier 1: Pre-Compute
    precompute_ms = book_stats.get("last_precompute_duration_ms", 0)
    precompute_at = book_stats.get("last_precompute_at", "never")
    if precompute_at and precompute_at != "never":
        pc_status = f"{book_stats['active_signals']} signals cached (last: {precompute_at[:19]})"
    else:
        pc_status = "[dim]Not run yet[/dim]"
    fast_table.add_row(
        "1. Pre-Compute",
        f"{precompute_ms:.0f}ms" if precompute_ms else "—",
        pc_status,
    )

    # Tier 2: Fast Path
    fast_table.add_row(
        "2. Fast Path",
        f"[green]{fast_ms:.1f}ms[/green]" if fast_ms else "[green]<0.1ms[/green]",
        f"Hit rate: {book_stats['hit_rate']:.0f}% ({book_stats['hits']}/{book_stats['lookups']})"
        if book_stats["lookups"] > 0
        else "[dim]No lookups yet[/dim]",
    )

    # Tier 3: Live LLM
    fast_table.add_row(
        "3. Analyst Swarm",
        f"[yellow]{swarm_ms:.0f}ms[/yellow]" if swarm_ms else "—",
        "Streaming + early extraction" if swarm_ms else "[dim]Not triggered[/dim]",
    )

    console.print(fast_table)

    overrides = book_stats.get("overrides_in_flight", 0)
    blocked = book_stats.get("blocked_by_override", 0)
    shock_inv = book_stats.get("shock_invalidations", 0)
    override_line = ""
    if overrides > 0 or blocked > 0 or shock_inv > 0:
        override_line = (
            f"\n  [yellow]Shock intercepts: {shock_inv} invalidations, "
            f"{blocked} fast-path blocks, "
            f"{overrides} overrides in-flight[/yellow]"
        )

    pending = get_signal_book().get_pending_overrides()
    pending_line = ""
    if pending:
        pending_line = (
            f"\n  [red bold]OVERRIDE ACTIVE: {', '.join(pending)} (fast path blocked)[/red bold]"
        )

    console.print(
        Panel(
            f"  Signal Book:  {book_stats['total_cached']} cached / "
            f"{book_stats['active_signals']} active\n"
            f"  Lookups:      {book_stats['lookups']} "
            f"(hit={book_stats['hits']}, miss={book_stats['misses']})\n"
            f"  Stale evict:  {book_stats['stale_evictions']}"
            f"{override_line}{pending_line}",
            title="[bold]Signal Book Stats[/bold]",
            border_style="dim",
        )
    )


@system_app.command("killswitch")
def killswitch():
    """Show kill switch status and read the seqlock mmap file."""
    from pathlib import Path

    from execution_engine.watchdog.mmap_bridge import (
        _MMAP_PATH_DEFAULT,
        RiskMmapBridge,
    )

    mmap_path = Path(_MMAP_PATH_DEFAULT)
    console.print(
        Panel(
            f"  Mmap Path:    {mmap_path}\n"
            f"  File Exists:  {mmap_path.exists()}\n"
            f"  C++ Watchdog: [dim]auto-compiled from execution_engine/watchdog/kill_switch.cpp[/dim]\n"
            f"  Writer Loop:  [dim]10Hz — started by backend lifespan[/dim]",
            title="[bold yellow]Kill Switch Status[/bold yellow]",
            border_style="yellow",
        )
    )

    if mmap_path.exists():
        try:
            reader = RiskMmapBridge.__new__(RiskMmapBridge)
            import mmap as _mmap

            reader._path = mmap_path
            reader._fd = open(mmap_path, "r+b")
            reader._mm = _mmap.mmap(reader._fd.fileno(), 0)
            reader._seq = 0
            data = reader.read()
            reader.close()
            if data:
                console.print(f"  Drawdown:     {data.get('drawdown_pct', 0):.2f}%")
                console.print(f"  VaR95:        {data.get('var_95', 0):.4f}")
                console.print(f"  Equity:       ${data.get('portfolio_equity', 0):,.0f}")
                console.print(f"  Armed:        {data.get('armed', False)}")
                console.print(f"  Python PID:   {data.get('python_pid', 0)}")
            else:
                console.print("  [dim]Torn read — writer is actively updating[/dim]")
        except Exception as e:
            console.print(f"  [dim]Cannot read mmap: {e}[/dim]")
