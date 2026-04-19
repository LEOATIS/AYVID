"""AYVID interactive shell — Gemini-CLI-style REPL.

When ``python -m cli.app`` (or just ``ayvid``) is invoked without a
subcommand, we drop the user into a persistent prompt instead of dumping
the splash and quitting back to the OS shell.

Design goals
------------
* Re-uses the existing Typer app (``cli.app:app``) so every registered
  command — ``data``, ``graph``, ``trade``, ``system``, ``dashboard`` —
  works identically inside the REPL with **no extra wiring**.
* Lines are tokenised with :mod:`shlex` so quoted arguments survive.
* A leading ``ayvid`` token is stripped, so muscle-memory pastes like
  ``ayvid trade paper-status`` keep working alongside the bare form
  ``trade paper-status``.
* The status bar (cwd · mode · model · clock) is reprinted before every
  prompt so it stays *live* even after long-running commands.
* Every command is followed by a slim "✓ command finished — ready" rule
  so the user always knows the REPL is alive and waiting, even when the
  command output is so tall it scrolls the prompt off-screen.
* Built-ins: ``help`` / ``?``, ``clear`` / ``cls``, ``exit`` / ``quit``
  / ``:q``. Ctrl+D and Ctrl+C both leave the shell cleanly.
"""
from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from cli.widgets.banner import print_banner, print_status_bar

if TYPE_CHECKING:  # avoid runtime import cycle with cli.app
    import typer

console = Console()

_BUILTIN_EXIT = {"exit", "quit", ":q", ":quit"}
_BUILTIN_CLEAR = {"clear", "cls"}
_BUILTIN_HELP = {"help", "?", ":h", "h"}


# ──────────────────────────────────────────────────────────────────
# In-shell help — short, AYVID-aware, never tells the user to prefix
# their commands with the word "ayvid". Keeps output small enough
# that the next prompt stays on-screen on a normal terminal.
# ──────────────────────────────────────────────────────────────────
_GROUP_HELP = [
    ("data", "Market data, universe scanning, backtesting, and pipeline ingestion"),
    ("graph", "Neo4j supply chain graph: init, seed, scan shocks, causal analysis"),
    ("trade", "Portfolio, signals, execution, Kelly sizing, risk metrics"),
    ("system", "Service health, config, API server, kill switch"),
    ("dashboard", "Launch the live monitoring dashboard"),
]
_BUILTIN_HELP_ROWS = [
    ("help", "Show this help (also: ?, h)"),
    ("clear", "Clear the screen and redraw the splash (also: cls)"),
    ("exit", "Leave the AYVID shell (also: quit, Ctrl+D)"),
]


def _print_shell_help() -> None:
    console.print()
    title = Text("AYVID interactive shell", style="bold #4ec9ff")
    console.print(title)
    console.print(
        Text(
            "  type a command — no `ayvid` prefix needed. "
            "Append --help to any group for its sub-commands.",
            style="dim #8a8aa0",
        )
    )

    cmd_tbl = Table.grid(padding=(0, 2))
    cmd_tbl.add_column(style="bold #c8a06a", no_wrap=True)
    cmd_tbl.add_column(style="#c8c8d4")
    cmd_tbl.add_row("Commands", "")
    for name, desc in _GROUP_HELP:
        cmd_tbl.add_row(f"  {name}", desc)
    console.print(cmd_tbl)

    builtin_tbl = Table.grid(padding=(0, 2))
    builtin_tbl.add_column(style="bold #a08acf", no_wrap=True)
    builtin_tbl.add_column(style="#c8c8d4")
    builtin_tbl.add_row("Built-ins", "")
    for name, desc in _BUILTIN_HELP_ROWS:
        builtin_tbl.add_row(f"  {name}", desc)
    console.print(builtin_tbl)

    console.print(
        Text(
            "  Examples:  trade paper-status   ·   data scan --top 10   ·   "
            "graph --help",
            style="dim #6e7080",
        )
    )


# ──────────────────────────────────────────────────────────────────
# Prompt scaffolding
# ──────────────────────────────────────────────────────────────────
def _print_prompt_intro() -> None:
    """Spacer + dynamic status bar before each prompt."""
    console.print()
    print_status_bar()


def _print_command_finished() -> None:
    """Slim divider after a command so the new prompt is unmistakable.

    This is the key UX cue: even when a command's output is taller than
    the terminal viewport (so the next prompt scrolls off the bottom on
    first render), the user can scroll down and see this rule + the
    fresh status bar and *know* the REPL is alive.
    """
    console.print(Rule(style="#2a2a3a"))


# ──────────────────────────────────────────────────────────────────
# Dispatch
# ──────────────────────────────────────────────────────────────────
def _dispatch(typer_app: "typer.Typer", line: str) -> None:
    """Run a single REPL line through the typer/click command tree."""
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        console.print(f"[red]✗ parse error: {exc}[/red]")
        return

    if tokens and tokens[0].lower() == "ayvid":
        tokens = tokens[1:]
    if not tokens:
        return

    # ``standalone_mode=False`` tells click NOT to call ``sys.exit()``,
    # but click still raises ``click.exceptions.Exit`` for ``--help`` /
    # ``--version`` paths and ``Abort`` for prompts. Catch them all so
    # the shell can never die in the middle of a session.
    try:
        typer_app(args=tokens, standalone_mode=False, prog_name="ayvid")
    except click.exceptions.UsageError as exc:
        console.print(f"[red]✗ {exc.format_message()}[/red]")
        console.print("[dim]Type 'help' for available commands.[/dim]")
    except click.exceptions.Exit:
        # --help / --version / explicit Exit() — totally normal, swallow.
        pass
    except click.exceptions.Abort:
        console.print("[dim]Aborted.[/dim]")
    except SystemExit as exc:
        if exc.code not in (0, None):
            console.print(f"[dim]Command exited with status {exc.code}.[/dim]")
    except KeyboardInterrupt:
        console.print("[dim]^C — command interrupted.[/dim]")
    except Exception as exc:  # pragma: no cover - defensive surface
        console.print(f"[red]✗ {type(exc).__name__}: {exc}[/red]")


# ──────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────
def run_shell(typer_app: "typer.Typer") -> None:
    """Block on the interactive AYVID shell until the user exits.

    Parameters
    ----------
    typer_app:
        The root :class:`typer.Typer` application (``cli.app:app``).
        Every line typed at the prompt is dispatched through it.
    """
    print_banner()

    while True:
        _print_prompt_intro()
        try:
            line = console.input("[bold #4ec9ff]ayvid[/bold #4ec9ff] [dim]›[/dim] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye.[/dim]\n")
            return

        line = line.strip()
        if not line:
            continue

        first = line.split(maxsplit=1)[0].lower()

        if first in _BUILTIN_EXIT:
            console.print("[dim]bye.[/dim]\n")
            return
        if first in _BUILTIN_CLEAR:
            console.clear()
            print_banner()
            continue
        if first in _BUILTIN_HELP and len(line.split()) == 1:
            # Bare ``help`` / ``?`` — print our compact in-shell guide
            # rather than dumping the full typer ``--help`` panel that
            # was so tall it scrolled the next prompt off-screen.
            _print_shell_help()
            _print_command_finished()
            continue

        _dispatch(typer_app, line)
        _print_command_finished()
