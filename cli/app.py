"""AYVID CLI — AI-Native Quantitative Hedge Fund Terminal.

Two-track institutional trading:
  Track 1: Index F&O — Futures and options on 20 global indices
           (NIFTY, S&P 500, DAX, FTSE, Nikkei, Hang Seng, etc.)
           with real expiry calendars, 2% risk lot sizing, and
           Black-Scholes option pricing.

  Track 2: Equity Investment — Long-term stock positions across
           50+ global equities, dynamically screened by an AI-native
           multi-factor ranker (momentum, value, quality, volume, volatility).

Powered by LangGraph agent swarms, Neo4j supply chain topology,
and N-th order causal arbitrage reasoning.
"""
from __future__ import annotations

import typer

from cli.commands.data import data_app
from cli.commands.graph import graph_app
from cli.commands.trade import trade_app
from cli.commands.system import system_app

app = typer.Typer(
    name="ayvid",
    help=(
        "AYVID — AI-Native Quantitative Hedge Fund.\n\n"
        "Two-track trading: Index F&O (20 world indices) + Equity Investment (50+ stocks).\n"
        "Supply chain graph intelligence | LangGraph agent swarms | Institutional risk management."
    ),
    no_args_is_help=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)

app.add_typer(data_app, name="data", help="Market data, universe scanning, backtesting, and pipeline ingestion")
app.add_typer(graph_app, name="graph", help="Neo4j supply chain graph: init, seed, scan shocks, causal analysis")
app.add_typer(trade_app, name="trade", help="Portfolio, signals, execution, Kelly sizing, risk metrics")
app.add_typer(system_app, name="system", help="Service health, config, API server, kill switch")


@app.command()
def dashboard():
    """Launch the live monitoring dashboard."""
    from cli.dashboard import run_dashboard
    run_dashboard()


# Module-level guard: prevents the no-arg callback from re-entering the
# REPL when the user types a bare `ayvid` (or empty line) *inside* the
# shell. Without this we'd recurse infinitely.
_IN_REPL = False


@app.callback()
def main(ctx: typer.Context):
    """AYVID — AI-Native Quantitative Hedge Fund.

    Two-track institutional trading engine with LangGraph agent swarms,
    Neo4j supply chain topology, and N-th order causal arbitrage.
    """
    global _IN_REPL
    if ctx.invoked_subcommand is not None:
        return
    if _IN_REPL:
        # User typed bare `ayvid` from inside the shell — just reprint the
        # splash without spawning a nested REPL.
        from cli.widgets.banner import print_banner
        print_banner()
        return

    _IN_REPL = True
    try:
        from cli.repl import run_shell
        run_shell(app)
    finally:
        _IN_REPL = False


if __name__ == "__main__":
    app()
