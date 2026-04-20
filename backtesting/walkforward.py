"""Walk-forward backtest runner.

Sweeps a rolling window across an arbitrary historical span (default
2018-01-01 → today) and runs a fresh out-of-sample backtest on each
window. Avoids the curve-fitting risk of cherry-picking famous shocks:
every window is treated identically and we report the entire
distribution of returns, Sharpes, and drawdowns.

Usage:
    from backtesting.walkforward import run_walk_forward
    summary = await run_walk_forward(
        start="2018-01-01",
        end="2025-12-31",
        window_months=6,
        step_months=3,
    )

CLI:
    ayvid data walkforward --start 2018-01-01 --window-months 6
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from backtesting.engine import (
    SHOCK_SCENARIOS,
    _DATA_DIR,
    _INDEX_DIR,
    run_backtest,
    run_scenario_backtest,
)
from shared.logger import get_logger

log = get_logger("walkforward")


# ── Shock-driven helpers ─────────────────────────────────────────
def _filter_to_cached(tickers: list[str], indices: list[str]) -> tuple[list[str], list[str]]:
    """Drop scenario tickers/indices we don't have in data_store/.

    The backtest engine handles missing data gracefully (auto-fetch tries
    Yahoo, then logs a warning), but in fully-offline runs we want to
    surface up-front exactly which symbols a scenario will actually trade.
    """
    cached_eq = {p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0}

    def _idx_safe(s: str) -> str:
        return s.replace("^", "").replace(".", "_")

    cached_idx = {p.stem for p in _INDEX_DIR.glob("*.jsonl") if p.stat().st_size > 0}
    eq_in = [t for t in tickers if t in cached_eq]
    idx_in = [s for s in indices if _idx_safe(s) in cached_idx]
    return eq_in, idx_in


@dataclass
class ShockResult:
    """Single shock-scenario outcome."""

    scenario_id: str
    name: str
    start_date: str
    end_date: str
    initial_equity: float
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    fno_trades: int
    equity_trades: int
    fno_pnl: float
    cost_drag_pct: float
    cached_tickers: int
    cached_indices: int
    shock_type: str = ""
    notes: str = ""


@dataclass
class ShockDrivenSummary:
    """Aggregate across all shock scenarios — chained as if portfolio."""

    catalog_size: int
    n_run: int
    n_valid: int
    span_start: str
    span_end: str
    initial_equity: float
    avg_return_pct: float
    median_return_pct: float
    pct_positive_scenarios: float
    avg_sharpe: float
    median_sharpe: float
    sharpe_above_one_pct: float
    worst_drawdown_pct: float
    avg_drawdown_pct: float
    pooled_total_trades: int
    pooled_win_rate: float
    avg_cost_drag_pct: float
    aggregate_compounded_return_pct: float
    aggregate_cagr_pct: float
    avg_trades_per_scenario: float
    scenarios: list[ShockResult] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["scenarios"] = [asdict(s) for s in self.scenarios]
        return d


@dataclass
class WindowResult:
    """Single walk-forward window outcome."""

    start_date: str
    end_date: str
    initial_equity: float
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    fno_trades: int
    equity_trades: int
    fno_pnl: float
    cost_drag_pct: float
    notes: str = ""


@dataclass
class WalkForwardSummary:
    """Aggregate distribution across all walk-forward windows."""

    start_date: str
    end_date: str
    window_months: int
    step_months: int
    initial_equity: float
    n_windows: int
    n_valid_windows: int
    avg_return_pct: float
    median_return_pct: float
    pct_positive_windows: float
    avg_sharpe: float
    median_sharpe: float
    sharpe_above_one_pct: float
    worst_drawdown_pct: float
    avg_drawdown_pct: float
    pooled_total_trades: int
    pooled_win_rate: float
    return_p10: float
    return_p25: float
    return_p75: float
    return_p90: float
    return_std: float
    avg_cost_drag_pct: float
    aggregate_compounded_return_pct: float
    aggregate_cagr_pct: float
    windows: list[WindowResult] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["windows"] = [asdict(w) for w in self.windows]
        return d


def _iter_windows(
    start: dt.date,
    end: dt.date,
    window_months: int,
    step_months: int,
) -> list[tuple[dt.date, dt.date]]:
    """Generate (window_start, window_end) tuples."""
    windows: list[tuple[dt.date, dt.date]] = []
    cur = start
    while cur < end:
        win_end_year = cur.year + (cur.month - 1 + window_months) // 12
        win_end_month = (cur.month - 1 + window_months) % 12 + 1
        try:
            win_end = dt.date(win_end_year, win_end_month, cur.day)
        except ValueError:
            win_end = dt.date(win_end_year, win_end_month, 28)
        win_end = min(win_end, end)
        if (win_end - cur).days < 20:
            break
        windows.append((cur, win_end))
        next_year = cur.year + (cur.month - 1 + step_months) // 12
        next_month = (cur.month - 1 + step_months) % 12 + 1
        try:
            cur = dt.date(next_year, next_month, cur.day)
        except ValueError:
            cur = dt.date(next_year, next_month, 28)
    return windows


async def run_walk_forward(
    start: str = "2018-01-01",
    end: str | None = None,
    window_months: int = 6,
    step_months: int = 3,
    initial_equity: float = 1_000_000.0,
    universe: list[str] | None = None,
    output_dir: Path = Path("."),
    min_conviction: float = 0.55,
    report_filename: str = "walkforward_report",
) -> WalkForwardSummary:
    """Run an out-of-sample walk-forward across a historical span.

    Each window starts with the same `initial_equity` so windows are
    comparable on a per-window basis. We also report the *compounded*
    aggregate return as if windows were chained sequentially (more
    representative of a live deployment).
    """
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end) if end else dt.date.today()
    windows = _iter_windows(start_d, end_d, window_months, step_months)

    log.info(
        f"Walk-forward: {len(windows)} windows of {window_months}m "
        f"stepping {step_months}m, {start_d} → {end_d}"
    )

    results: list[WindowResult] = []
    compounded_equity = initial_equity

    for i, (ws, we) in enumerate(windows, 1):
        log.info(f"[{i}/{len(windows)}] window {ws} → {we}")
        try:
            r = await run_backtest(
                tickers=universe,
                initial_equity=initial_equity,
                start_date=ws.isoformat(),
                end_date=we.isoformat(),
                min_conviction=min_conviction,
            )
        except Exception as exc:
            log.warning(f"window {ws} → {we} failed: {exc}")
            results.append(WindowResult(
                start_date=ws.isoformat(),
                end_date=we.isoformat(),
                initial_equity=initial_equity,
                final_equity=initial_equity,
                total_return_pct=0.0,
                sharpe_ratio=0.0,
                max_drawdown_pct=0.0,
                win_rate=0.0,
                profit_factor=0.0,
                total_trades=0,
                fno_trades=0,
                equity_trades=0,
                fno_pnl=0.0,
                cost_drag_pct=0.0,
                notes=f"FAILED: {exc}",
            ))
            continue

        tx = r.extra_stats.get("transaction_costs", {}) or {}
        compounded_equity *= (1.0 + r.total_return_pct / 100.0)
        results.append(WindowResult(
            start_date=ws.isoformat(),
            end_date=we.isoformat(),
            initial_equity=initial_equity,
            final_equity=round(r.final_equity, 2),
            total_return_pct=round(r.total_return_pct, 2),
            sharpe_ratio=round(r.sharpe_ratio, 2),
            max_drawdown_pct=round(r.max_drawdown_pct, 2),
            win_rate=round(r.win_rate, 2),
            profit_factor=round(r.profit_factor, 2),
            total_trades=r.total_trades,
            fno_trades=r.extra_stats.get("fno_trades", 0),
            equity_trades=r.extra_stats.get("equity_trades", 0),
            fno_pnl=round(r.extra_stats.get("fno_pnl", 0.0), 2),
            cost_drag_pct=round(tx.get("cost_drag_pct", 0.0), 3),
        ))

    valid = [w for w in results if not w.notes.startswith("FAILED")]
    rets = [w.total_return_pct for w in valid]
    sharpes = [w.sharpe_ratio for w in valid]
    dds = [w.max_drawdown_pct for w in valid]
    n_total_trades = sum(w.total_trades for w in valid)
    pooled_wins = sum(int(w.win_rate * w.total_trades / 100) for w in valid)

    span_years = max((end_d - start_d).days / 365.25, 1e-6)
    aggregate_compounded = (compounded_equity / initial_equity - 1.0) * 100.0
    cagr = ((compounded_equity / initial_equity) ** (1.0 / span_years) - 1.0) * 100.0 if span_years > 0 else 0.0

    summary = WalkForwardSummary(
        start_date=start_d.isoformat(),
        end_date=end_d.isoformat(),
        window_months=window_months,
        step_months=step_months,
        initial_equity=initial_equity,
        n_windows=len(results),
        n_valid_windows=len(valid),
        avg_return_pct=round(statistics.mean(rets), 2) if rets else 0.0,
        median_return_pct=round(statistics.median(rets), 2) if rets else 0.0,
        pct_positive_windows=round(
            len([r for r in rets if r > 0]) / max(len(rets), 1) * 100, 1
        ),
        avg_sharpe=round(statistics.mean(sharpes), 2) if sharpes else 0.0,
        median_sharpe=round(statistics.median(sharpes), 2) if sharpes else 0.0,
        sharpe_above_one_pct=round(
            len([s for s in sharpes if s > 1.0]) / max(len(sharpes), 1) * 100, 1
        ),
        worst_drawdown_pct=round(max(dds), 2) if dds else 0.0,
        avg_drawdown_pct=round(statistics.mean(dds), 2) if dds else 0.0,
        pooled_total_trades=n_total_trades,
        pooled_win_rate=round(pooled_wins / max(n_total_trades, 1) * 100, 1),
        return_p10=round(float(np.percentile(rets, 10)), 2) if rets else 0.0,
        return_p25=round(float(np.percentile(rets, 25)), 2) if rets else 0.0,
        return_p75=round(float(np.percentile(rets, 75)), 2) if rets else 0.0,
        return_p90=round(float(np.percentile(rets, 90)), 2) if rets else 0.0,
        return_std=round(float(np.std(rets, ddof=1)), 2) if len(rets) > 1 else 0.0,
        avg_cost_drag_pct=round(
            statistics.mean(w.cost_drag_pct for w in valid), 3
        ) if valid else 0.0,
        aggregate_compounded_return_pct=round(aggregate_compounded, 2),
        aggregate_cagr_pct=round(cagr, 2),
        windows=results,
        generated_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report_filename}.json"
    json_path.write_text(json.dumps(summary.to_dict(), indent=2, default=str) + "\n")

    md_path = output_dir / f"{report_filename}.md"
    md_path.write_text(_render_markdown(summary, min_conviction=min_conviction))

    log.info(
        f"Walk-forward complete: {summary.n_valid_windows}/{summary.n_windows} valid "
        f"windows, avg ret={summary.avg_return_pct:+.2f}%, "
        f"median Sharpe={summary.median_sharpe:.2f}, "
        f"{summary.pct_positive_windows:.0f}% positive, "
        f"compounded={summary.aggregate_compounded_return_pct:+.2f}% / "
        f"CAGR={summary.aggregate_cagr_pct:+.2f}%"
    )
    return summary


def _render_markdown(summary: WalkForwardSummary, min_conviction: float = 0.55) -> str:
    lines: list[str] = []
    lines.append("# AYVID Walk-Forward Out-of-Sample Report")
    lines.append("")
    lines.append(f"*Generated: {summary.generated_at}*")
    lines.append(f"*Min conviction filter: {min_conviction:.2f}*")
    lines.append("")
    lines.append(
        "Out-of-sample sweep across the entire historical span. "
        "Every window is treated identically — no cherry-picked shocks, "
        "no in-sample overfitting. PnL is post-cost using the IBKR Tiered "
        "transaction model."
    )
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|-----------|-------|")
    lines.append(f"| Span | {summary.start_date} → {summary.end_date} |")
    lines.append(f"| Window length | {summary.window_months} months |")
    lines.append(f"| Step | {summary.step_months} months |")
    lines.append(f"| Initial equity per window | ${summary.initial_equity:,.0f} |")
    lines.append(f"| Windows | {summary.n_valid_windows} valid / {summary.n_windows} total |")
    lines.append("")

    lines.append("## Distribution of Outcomes")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Avg return per window | {summary.avg_return_pct:+.2f}% |")
    lines.append(f"| Median return per window | {summary.median_return_pct:+.2f}% |")
    lines.append(f"| Std dev of returns | {summary.return_std:.2f}% |")
    lines.append(f"| 10th / 25th / 75th / 90th pct | "
                 f"{summary.return_p10:+.2f}% / {summary.return_p25:+.2f}% / "
                 f"{summary.return_p75:+.2f}% / {summary.return_p90:+.2f}% |")
    lines.append(f"| % positive windows | {summary.pct_positive_windows:.1f}% |")
    lines.append(f"| Avg Sharpe | {summary.avg_sharpe:.2f} |")
    lines.append(f"| Median Sharpe | {summary.median_sharpe:.2f} |")
    lines.append(f"| % Sharpe > 1.0 | {summary.sharpe_above_one_pct:.1f}% |")
    lines.append(f"| Worst single-window drawdown | {summary.worst_drawdown_pct:.2f}% |")
    lines.append(f"| Avg drawdown | {summary.avg_drawdown_pct:.2f}% |")
    lines.append(f"| Pooled trades | {summary.pooled_total_trades:,} |")
    lines.append(f"| Pooled win rate | {summary.pooled_win_rate:.1f}% |")
    lines.append(f"| Avg cost drag per window | {summary.avg_cost_drag_pct:.3f}% |")
    lines.append("")

    lines.append("## Compounded Aggregate (chained windows)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total compounded return | {summary.aggregate_compounded_return_pct:+.2f}% |")
    lines.append(f"| CAGR | {summary.aggregate_cagr_pct:+.2f}% |")
    lines.append("")

    lines.append("## Per-Window Results")
    lines.append("")
    lines.append("| Window | Return | Sharpe | Max DD | Win Rate | Trades | Cost Drag |")
    lines.append("|--------|--------|--------|--------|----------|--------|-----------|")
    for w in summary.windows:
        if w.notes:
            lines.append(
                f"| {w.start_date} → {w.end_date} | — | — | — | — | — | {w.notes} |"
            )
        else:
            lines.append(
                f"| {w.start_date} → {w.end_date} | "
                f"{w.total_return_pct:+.2f}% | {w.sharpe_ratio:.2f} | "
                f"{w.max_drawdown_pct:.2f}% | {w.win_rate:.0f}% | "
                f"{w.total_trades} | {w.cost_drag_pct:.3f}% |"
            )
    lines.append("")
    lines.append("---")
    lines.append("*AYVID Walk-Forward — out-of-sample, post-cost*")
    lines.append("")
    return "\n".join(lines)


# ── Shock-driven aggregator ──────────────────────────────────────
async def run_shock_driven_walkforward(
    start: str = "2018-01-01",
    end: str | None = None,
    initial_equity: float = 1_000_000.0,
    min_conviction: float = 0.55,
    output_dir: Path = Path("."),
    report_filename: str = "shock_driven_report",
    only: list[str] | None = None,
) -> ShockDrivenSummary:
    """Run AYVID's *true* operating mode: trade only when the graph
    identifies a real causal shock, only on the causally-linked tickers
    the graph surfaces.

    Iterates the named historical shock catalog (``SHOCK_SCENARIOS`` in
    ``backtesting/engine.py``), runs ``run_scenario_backtest`` for each
    one whose date range overlaps ``[start, end]``, and aggregates the
    portfolio as if AYVID were running continuously and reallocating
    capital through each shock.

    Compared with the naive everyday-trading walkforward, this is what
    the system is *designed* to do — selective, event-driven trades on
    causally-linked tickers only.
    """
    span_start = dt.date.fromisoformat(start)
    span_end = dt.date.fromisoformat(end) if end else dt.date.today()

    eligible: list[tuple[str, dict]] = []
    for sid, scen in SHOCK_SCENARIOS.items():
        s_start = dt.date.fromisoformat(scen["start_date"])
        s_end = dt.date.fromisoformat(scen["end_date"])
        if s_end < span_start or s_start > span_end:
            continue
        if only and sid not in only:
            continue
        eligible.append((sid, scen))

    eligible.sort(key=lambda x: x[1]["start_date"])

    log.info(
        f"Shock-driven walk-forward: {len(eligible)} scenarios in "
        f"{span_start} → {span_end}"
    )

    results: list[ShockResult] = []
    portfolio_equity = initial_equity

    for i, (sid, scen) in enumerate(eligible, 1):
        cached_eq, cached_idx = _filter_to_cached(scen["tickers"], scen.get("indices", []))
        log.info(
            f"[{i}/{len(eligible)}] {scen['name']} "
            f"({scen['start_date']}→{scen['end_date']}) — "
            f"{len(cached_eq)}/{len(scen['tickers'])} tickers, "
            f"{len(cached_idx)}/{len(scen.get('indices', []))} indices cached"
        )

        if not cached_eq and not cached_idx:
            results.append(ShockResult(
                scenario_id=sid,
                name=scen["name"],
                start_date=scen["start_date"],
                end_date=scen["end_date"],
                initial_equity=initial_equity,
                final_equity=initial_equity,
                total_return_pct=0.0,
                sharpe_ratio=0.0,
                max_drawdown_pct=0.0,
                win_rate=0.0,
                profit_factor=0.0,
                total_trades=0,
                fno_trades=0,
                equity_trades=0,
                fno_pnl=0.0,
                cost_drag_pct=0.0,
                cached_tickers=0,
                cached_indices=0,
                shock_type=scen.get("causal_intel", {}).get("shock_type", ""),
                notes="SKIPPED: no cached data",
            ))
            continue

        try:
            r = await run_scenario_backtest(
                sid,
                initial_equity=initial_equity,
                min_conviction=min_conviction,
            )
        except Exception as exc:
            log.warning(f"  scenario {sid} failed: {exc}")
            results.append(ShockResult(
                scenario_id=sid,
                name=scen["name"],
                start_date=scen["start_date"],
                end_date=scen["end_date"],
                initial_equity=initial_equity,
                final_equity=initial_equity,
                total_return_pct=0.0,
                sharpe_ratio=0.0,
                max_drawdown_pct=0.0,
                win_rate=0.0,
                profit_factor=0.0,
                total_trades=0,
                fno_trades=0,
                equity_trades=0,
                fno_pnl=0.0,
                cost_drag_pct=0.0,
                cached_tickers=len(cached_eq),
                cached_indices=len(cached_idx),
                shock_type=scen.get("causal_intel", {}).get("shock_type", ""),
                notes=f"FAILED: {exc}",
            ))
            continue

        tx = r.extra_stats.get("transaction_costs", {}) or {}
        portfolio_equity *= (1.0 + r.total_return_pct / 100.0)
        results.append(ShockResult(
            scenario_id=sid,
            name=scen["name"],
            start_date=scen["start_date"],
            end_date=scen["end_date"],
            initial_equity=initial_equity,
            final_equity=round(r.final_equity, 2),
            total_return_pct=round(r.total_return_pct, 2),
            sharpe_ratio=round(r.sharpe_ratio, 2),
            max_drawdown_pct=round(r.max_drawdown_pct, 2),
            win_rate=round(r.win_rate, 2),
            profit_factor=round(r.profit_factor, 2),
            total_trades=r.total_trades,
            fno_trades=r.extra_stats.get("fno_trades", 0),
            equity_trades=r.extra_stats.get("equity_trades", 0),
            fno_pnl=round(r.extra_stats.get("fno_pnl", 0.0), 2),
            cost_drag_pct=round(tx.get("cost_drag_pct", 0.0), 3),
            cached_tickers=len(cached_eq),
            cached_indices=len(cached_idx),
            shock_type=scen.get("causal_intel", {}).get("shock_type", ""),
        ))

    valid = [s for s in results if not (s.notes or "").startswith(("FAILED", "SKIPPED"))]
    rets = [s.total_return_pct for s in valid]
    sharpes = [s.sharpe_ratio for s in valid]
    dds = [s.max_drawdown_pct for s in valid]
    n_total_trades = sum(s.total_trades for s in valid)
    pooled_wins = sum(int(s.win_rate * s.total_trades / 100) for s in valid)

    span_years = max((span_end - span_start).days / 365.25, 1e-6)
    aggregate_compounded = (portfolio_equity / initial_equity - 1.0) * 100.0
    cagr = ((portfolio_equity / initial_equity) ** (1.0 / span_years) - 1.0) * 100.0

    summary = ShockDrivenSummary(
        catalog_size=len(SHOCK_SCENARIOS),
        n_run=len(results),
        n_valid=len(valid),
        span_start=span_start.isoformat(),
        span_end=span_end.isoformat(),
        initial_equity=initial_equity,
        avg_return_pct=round(statistics.mean(rets), 2) if rets else 0.0,
        median_return_pct=round(statistics.median(rets), 2) if rets else 0.0,
        pct_positive_scenarios=round(
            len([r for r in rets if r > 0]) / max(len(rets), 1) * 100, 1
        ),
        avg_sharpe=round(statistics.mean(sharpes), 2) if sharpes else 0.0,
        median_sharpe=round(statistics.median(sharpes), 2) if sharpes else 0.0,
        sharpe_above_one_pct=round(
            len([s for s in sharpes if s > 1.0]) / max(len(sharpes), 1) * 100, 1
        ),
        worst_drawdown_pct=round(max(dds), 2) if dds else 0.0,
        avg_drawdown_pct=round(statistics.mean(dds), 2) if dds else 0.0,
        pooled_total_trades=n_total_trades,
        pooled_win_rate=round(pooled_wins / max(n_total_trades, 1) * 100, 1),
        avg_cost_drag_pct=round(
            statistics.mean(s.cost_drag_pct for s in valid), 3
        ) if valid else 0.0,
        aggregate_compounded_return_pct=round(aggregate_compounded, 2),
        aggregate_cagr_pct=round(cagr, 2),
        avg_trades_per_scenario=round(n_total_trades / max(len(valid), 1), 1),
        scenarios=results,
        generated_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report_filename}.json"
    json_path.write_text(json.dumps(summary.to_dict(), indent=2, default=str) + "\n")

    md_path = output_dir / f"{report_filename}.md"
    md_path.write_text(_render_shock_markdown(summary, min_conviction=min_conviction))

    log.info(
        f"Shock-driven complete: {summary.n_valid}/{summary.n_run} valid scenarios, "
        f"avg ret={summary.avg_return_pct:+.2f}%, "
        f"median Sharpe={summary.median_sharpe:.2f}, "
        f"{summary.pct_positive_scenarios:.0f}% positive, "
        f"compounded={summary.aggregate_compounded_return_pct:+.2f}% / "
        f"CAGR={summary.aggregate_cagr_pct:+.2f}%, "
        f"avg trades/scenario={summary.avg_trades_per_scenario:.1f}"
    )
    return summary


def _render_shock_markdown(summary: ShockDrivenSummary, min_conviction: float = 0.55) -> str:
    lines: list[str] = []
    lines.append("# AYVID Shock-Driven Walk-Forward Report")
    lines.append("")
    lines.append(f"*Generated: {summary.generated_at}*")
    lines.append(f"*Min conviction filter: {min_conviction:.2f}*")
    lines.append("")
    lines.append(
        "**This is AYVID's actual operating mode.** Rather than blindly "
        "trading every ticker every day on technicals (the naive "
        "walk-forward baseline), this report restricts the system to "
        "named historical shocks where the graph layer identified a real "
        "causal event, and only trades the tickers the graph surfaced as "
        "causally linked. PnL is post-cost using the IBKR Tiered model."
    )
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|-----------|-------|")
    lines.append(f"| Span | {summary.span_start} → {summary.span_end} |")
    lines.append(f"| Catalog size | {summary.catalog_size} named shocks |")
    lines.append(f"| Scenarios run | {summary.n_run} |")
    lines.append(f"| Valid (non-empty) | {summary.n_valid} |")
    lines.append(f"| Initial equity per scenario | ${summary.initial_equity:,.0f} |")
    lines.append(f"| Avg trades / scenario | {summary.avg_trades_per_scenario:.1f} |")
    lines.append(f"| Pooled trades total | {summary.pooled_total_trades:,} |")
    lines.append("")
    lines.append("## Distribution of Outcomes")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Avg return per scenario | {summary.avg_return_pct:+.2f}% |")
    lines.append(f"| Median return per scenario | {summary.median_return_pct:+.2f}% |")
    lines.append(f"| % positive scenarios | {summary.pct_positive_scenarios:.1f}% |")
    lines.append(f"| Avg Sharpe | {summary.avg_sharpe:.2f} |")
    lines.append(f"| Median Sharpe | {summary.median_sharpe:.2f} |")
    lines.append(f"| % Sharpe > 1.0 | {summary.sharpe_above_one_pct:.1f}% |")
    lines.append(f"| Worst single-scenario DD | {summary.worst_drawdown_pct:.2f}% |")
    lines.append(f"| Avg drawdown | {summary.avg_drawdown_pct:.2f}% |")
    lines.append(f"| Pooled win rate | {summary.pooled_win_rate:.1f}% |")
    lines.append(f"| Avg cost drag / scenario | {summary.avg_cost_drag_pct:.3f}% |")
    lines.append("")
    lines.append("## Compounded Aggregate (chained scenarios)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total compounded return | {summary.aggregate_compounded_return_pct:+.2f}% |")
    lines.append(f"| CAGR | {summary.aggregate_cagr_pct:+.2f}% |")
    lines.append("")
    lines.append("## Per-Scenario Results")
    lines.append("")
    lines.append("| Scenario | Window | Return | Sharpe | Max DD | Trades | Tickers | Notes |")
    lines.append("|----------|--------|--------|--------|--------|--------|---------|-------|")
    for s in summary.scenarios:
        if s.notes:
            lines.append(
                f"| {s.name} | {s.start_date} → {s.end_date} | — | — | — | — | "
                f"{s.cached_tickers}eq+{s.cached_indices}idx | {s.notes} |"
            )
        else:
            lines.append(
                f"| {s.name} | {s.start_date} → {s.end_date} | "
                f"{s.total_return_pct:+.2f}% | {s.sharpe_ratio:.2f} | "
                f"{s.max_drawdown_pct:.2f}% | {s.total_trades} | "
                f"{s.cached_tickers}eq+{s.cached_indices}idx | {s.shock_type} |"
            )
    lines.append("")
    lines.append("---")
    lines.append("*AYVID Shock-Driven Walk-Forward — selective, event-driven, post-cost*")
    lines.append("")
    return "\n".join(lines)
