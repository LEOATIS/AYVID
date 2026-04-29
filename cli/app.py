"""AYVID CLI — AI-Native Quantitative Hedge Fund Terminal.

Multi-asset institutional trading across 6 asset classes:
  Equities, Index F&O, FX, Commodities, Crypto, ETFs & Bonds.

  Index F&O — Futures and options on 20 global indices
           (NIFTY, S&P 500, DAX, FTSE, Nikkei, Hang Seng, etc.)
           with real expiry calendars, 2% risk lot sizing, and
           Black-Scholes option pricing.

  Equity Investment — Long-term stock positions across
           50+ global equities, dynamically screened by an AI-native
           multi-factor ranker (momentum, value, quality, volume, volatility).

Powered by LangGraph agent swarms, Neo4j supply chain topology,
and N-th order causal arbitrage reasoning.

Entry-point hardening: this module wraps the Typer app in a top-level
exception handler so unhandled errors produce a structured, exit-code-
mapped termination instead of a raw Python traceback on stderr. The
exit codes are enumerated in ``cli.exit_codes``; every command that
raises ``typer.Exit`` MUST import its code from there.
"""

from __future__ import annotations

import sys
import traceback

import typer
from rich.console import Console

from cli.commands.data import data_app
from cli.commands.graph import graph_app
from cli.commands.system import system_app
from cli.commands.trade import trade_app
from cli.exit_codes import (
    EXIT_GENERIC_ERROR,
    EXIT_KEYBOARD_INTERRUPT,
)

_err_console = Console(stderr=True)

app = typer.Typer(
    name="ayvid",
    help=(
        "AYVID — AI-Native Quantitative Hedge Fund.\n\n"
        "Multi-asset trading: 6 asset classes (equities, index F&O, FX, commodities, crypto, ETFs & bonds).\n"
        "Supply chain graph intelligence | LangGraph agent swarms | Institutional risk management."
    ),
    no_args_is_help=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)

app.add_typer(
    data_app,
    name="data",
    help="Market data, universe scanning, backtesting, and pipeline ingestion",
)
app.add_typer(
    graph_app,
    name="graph",
    help="Neo4j supply chain graph: init, seed, scan shocks, causal analysis",
)
app.add_typer(
    trade_app, name="trade", help="Portfolio, signals, execution, Kelly sizing, risk metrics"
)
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

    Multi-asset institutional trading engine (6 asset classes) with LangGraph agent swarms,
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


def _run() -> int:
    """Top-level CLI entry point — exceptions → structured exit codes.

    Handles three outcome classes:
      * ``SystemExit`` / ``typer.Exit`` — the command already decided
        its code; pass it through unchanged.
      * ``KeyboardInterrupt`` — the operator hit Ctrl-C; print a one-
        line note on stderr and exit 130 (the bash convention).
      * Any other ``Exception`` — unhandled. Print a short, non-leaky
        summary on stderr (full traceback only when ``AYVID_DEBUG=1``)
        and exit ``EXIT_GENERIC_ERROR``. We deliberately do NOT print
        secrets or environment state — the operator can rerun with
        ``AYVID_DEBUG=1`` to get the full stack.
    """
    import os

    try:
        app()
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_GENERIC_ERROR
        return code
    except KeyboardInterrupt:
        _err_console.print("\n[yellow]Interrupted by user.[/yellow]")
        return EXIT_KEYBOARD_INTERRUPT
    except Exception as exc:  # noqa: BLE001
        debug = os.environ.get("AYVID_DEBUG", "").strip() not in ("", "0", "false", "False")
        if debug:
            _err_console.print(f"[bold red]UNHANDLED {type(exc).__name__}[/bold red]: {exc}\n")
            _err_console.print(traceback.format_exc())
        else:
            _err_console.print(
                f"[bold red]Error:[/bold red] {type(exc).__name__}: {exc}\n"
                f"[dim]Rerun with AYVID_DEBUG=1 for the full traceback.[/dim]"
            )
        return EXIT_GENERIC_ERROR


if __name__ == "__main__":
    sys.exit(_run())
