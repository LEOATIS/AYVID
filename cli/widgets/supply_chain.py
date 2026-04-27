"""ASCII supply chain topology visualization for the CLI."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from shared.types import CausalPath

console = Console()


def render_causal_paths(
    shock_ticker: str,
    paths: list[CausalPath],
) -> Panel:
    """Render discovered causal paths as a Rich tree."""
    tree = Tree(
        f"[bold red]SHOCK: {shock_ticker}[/bold red]",
        guide_style="red",
    )

    for i, path in enumerate(paths[:10]):
        risk_color = (
            "green"
            if path.compound_risk < 0.2
            else ("yellow" if path.compound_risk < 0.4 else "red")
        )

        branch = tree.add(
            f"[{risk_color}]Path {i + 1}[/{risk_color}] "
            f"(depth={path.depth}, risk={path.compound_risk:.3f})"
        )

        for j, ticker in enumerate(path.path_tickers):
            if j == 0:
                style = "bold red"
                label = f"{ticker} [SHOCK SOURCE]"
            elif j == len(path.path_tickers) - 1:
                style = "bold cyan"
                label = f"{ticker} [TARGET]"
            else:
                style = "dim"
                label = f"{ticker}"

            if j < len(path.path_tickers) - 1:
                branch.add(f"[{style}]{label}[/{style}]")
            else:
                branch.add(f"[{style}]{label}[/{style}]")

    return Panel(
        tree,
        title="[bold]Supply Chain Causal Paths[/bold]",
        subtitle=f"{len(paths)} paths discovered via Systemic Importance Masking",
        border_style="cyan",
    )


def render_supply_chain_ascii(
    shock_ticker: str,
    paths: list[CausalPath],
) -> Panel:
    """Render a simplified ASCII graph of the supply chain."""
    lines: list[str] = []

    seen_edges: set[tuple[str, str]] = set()

    for path in paths[:5]:
        tickers = path.path_tickers
        for i in range(len(tickers) - 1):
            edge = (tickers[i], tickers[i + 1])
            if edge not in seen_edges:
                seen_edges.add(edge)
                dep = path.compound_risk ** (1.0 / max(path.depth, 1))
                lines.append(f"  {tickers[i]:>8s} ──[{dep:.2f}]──> {tickers[i + 1]}")

    content = "\n".join(lines) if lines else "  No supply chain data available"

    return Panel(
        content,
        title=f"[bold]Topology from {shock_ticker}[/bold]",
        border_style="cyan",
    )
