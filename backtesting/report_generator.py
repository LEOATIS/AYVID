"""YC-ready performance report generator.

Produces structured, institutional-grade reports from BacktestResult objects
with strategy attribution, per-index P&L, risk metrics, and alpha decomposition.

Output formats:
- JSON  (machine-readable, for YC deck data pipelines)
- Markdown (human-readable, for pitch appendices)
- TXT  (concise terminal-friendly summary via ``generate_backtest_txt``)
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from backtesting.engine import BacktestResult
from shared.logger import get_logger
from shared.types import OHLCVBar

log = get_logger("report")


# ── Helpers ───────────────────────────────────────────────────


def _format_fno_trade(ft: Any) -> dict[str, Any]:
    """Build a rich, option-chain-aware trade detail dict for a single F&O trade."""
    d = ft.details or {}
    base: dict[str, Any] = {
        "underlying": ft.underlying,
        "instrument": ft.instrument,
        "direction": ft.direction,
        "trade_mode": d.get("trade_mode", "INTRADAY"),
        "spot_entry": d.get("spot_entry"),
        "spot_exit": d.get("spot_exit"),
        "entry_date": str(ft.entry_date),
        "exit_date": str(ft.exit_date),
        "lots": d.get("lots", 1),
        "multiplier": d.get("multiplier"),
        "pnl": _safe_round(ft.pnl),
        "pnl_pct": _safe_round(ft.pnl_pct, 1),
        "risk_1R_usd": d.get("risk_1R_usd"),
        "realized_r": d.get("realized_r"),
        "hold_days": ft.hold_days,
        "exit_reason": d.get("exit_reason", ""),
        "conviction": d.get("conviction"),
        "strategy": ft.strategy,
    }
    if ft.instrument in ("INDEX_CALL", "INDEX_PUT"):
        base["opt_type"] = d.get("opt_type", "CALL" if "CALL" in ft.instrument else "PUT")
        base["strike"] = d.get("strike")
        base["entry_premium"] = d.get("entry_premium")
        base["exit_premium"] = d.get("exit_premium")
        base["iv"] = d.get("iv")
        base["expiry_date"] = d.get("expiry_date")
        base["dte_at_entry"] = d.get("dte_at_entry")
    elif ft.instrument == "INDEX_SPREAD":
        base["opt_type"] = d.get("opt_type")
        base["long_strike"] = d.get("long_strike")
        base["short_strike"] = d.get("short_strike")
        base["spread_width"] = d.get("spread_width")
        base["long_prem"] = d.get("long_prem")
        base["short_prem"] = d.get("short_prem")
        base["net_debit"] = d.get("net_debit")
        base["exit_spread_val"] = d.get("exit_spread_val")
        base["expiry_date"] = d.get("expiry_date")
        base["dte_at_entry"] = d.get("dte_at_entry")
    elif ft.instrument == "INDEX_FUTURE":
        base["fut_entry"] = d.get("fut_entry")
        base["fut_exit"] = d.get("fut_exit")
        base["pnl_pts"] = d.get("pnl_pts")
        base["margin_per_lot"] = d.get("margin_per_lot")
        base["expiry_date"] = d.get("expiry_date")
        base["dte_at_entry"] = d.get("dte_at_entry")
    elif ft.instrument == "FX":
        base["pair"] = d.get("pair", ft.underlying)
        base["entry_rate"] = d.get("entry_rate")
        base["exit_rate"] = d.get("exit_rate")
        base["lot_size"] = d.get("lot_size")
    elif ft.instrument == "COMMODITY_FUTURE":
        base["contract"] = d.get("contract", ft.underlying)
        base["entry_price"] = d.get("entry_price")
        base["exit_price"] = d.get("exit_price")
        base["tick_value"] = d.get("tick_value")
    elif ft.instrument == "CRYPTO":
        base["asset"] = d.get("asset", ft.underlying)
        base["entry_price"] = d.get("entry_price")
        base["exit_price"] = d.get("exit_price")
        base["notional"] = d.get("notional")
    elif ft.instrument == "ETF":
        base["ticker"] = d.get("ticker", ft.underlying)
        base["entry_price"] = d.get("entry_price")
        base["exit_price"] = d.get("exit_price")
        base["shares"] = d.get("shares")
    elif ft.instrument == "RATE_FUTURE":
        base["contract"] = d.get("contract", ft.underlying)
        base["tick_value"] = d.get("tick_value")
        base["margin_per_lot"] = d.get("margin_per_lot")
    return base


def _safe_round(val: float | None, decimals: int = 2) -> float:
    if val is None:
        return 0.0
    return round(float(val), decimals)


def _sortino_ratio(daily_returns: np.ndarray) -> float:
    """Sortino ratio: mean / downside deviation (annualised)."""
    if len(daily_returns) < 2:
        return 0.0
    downside = daily_returns[daily_returns < 0]
    if len(downside) < 1:
        return 0.0
    dd_std = float(np.std(downside, ddof=1))
    if dd_std < 1e-10:
        return 0.0
    return float(np.mean(daily_returns) / dd_std * np.sqrt(252))


def _calmar_ratio(annualised_return: float, max_dd_pct: float) -> float:
    if max_dd_pct < 0.01:
        return 0.0
    return round(annualised_return / max_dd_pct, 2)


def _max_consecutive(bools: list[bool]) -> int:
    """Max consecutive True values."""
    mx, cur = 0, 0
    for v in bools:
        cur = cur + 1 if v else 0
        mx = max(mx, cur)
    return mx


# ── Buy-and-Hold Benchmark ────────────────────────────────────


def compute_buy_and_hold(
    bars_by_ticker: dict[str, list[OHLCVBar]],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Equal-weight buy-and-hold benchmark."""
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)

    per_ticker: list[dict[str, Any]] = []
    for ticker, bars in bars_by_ticker.items():
        period_bars = [b for b in bars if start <= b.timestamp.date() <= end]
        if len(period_bars) < 2:
            continue
        first_close = period_bars[0].close
        last_close = period_bars[-1].close
        ret = (last_close / first_close - 1) * 100

        closes = [b.close for b in period_bars]
        peak = closes[0]
        max_dd = 0.0
        for c in closes:
            peak = max(peak, c)
            dd = (peak - c) / peak * 100
            max_dd = max(max_dd, dd)

        per_ticker.append(
            {
                "ticker": ticker,
                "return_pct": round(ret, 2),
                "start_price": round(first_close, 2),
                "end_price": round(last_close, 2),
                "max_drawdown_pct": round(max_dd, 2),
            }
        )

    if not per_ticker:
        return {"portfolio_return_pct": 0.0, "per_ticker": [], "count": 0}

    avg_return = float(np.mean([t["return_pct"] for t in per_ticker]))
    avg_dd = float(np.mean([t["max_drawdown_pct"] for t in per_ticker]))

    per_ticker.sort(key=lambda t: t["return_pct"])
    return {
        "portfolio_return_pct": round(avg_return, 2),
        "avg_max_drawdown_pct": round(avg_dd, 2),
        "count": len(per_ticker),
        "best": per_ticker[-1] if per_ticker else None,
        "worst": per_ticker[0] if per_ticker else None,
        "per_ticker": per_ticker,
    }


# ── Strategy Attribution ──────────────────────────────────────


def _strategy_attribution(result: BacktestResult) -> list[dict[str, Any]]:
    """PnL breakdown by strategy name across both equity and F&O trades."""
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "total_r": 0.0, "r_count": 0}
    )

    for trade in result.trades:
        name = (
            trade.signal.rationale.split("]")[0].lstrip("[")
            if trade.signal.rationale.startswith("[")
            else "equity_other"
        )
        b = buckets[name]
        b["pnl"] += trade.pnl
        b["trades"] += 1
        if trade.pnl > 0:
            b["wins"] += 1

    for ft in result.fno_trade_log:
        name = ft.strategy or "fno_other"
        b = buckets[name]
        b["pnl"] += ft.pnl
        b["trades"] += 1
        if ft.pnl > 0:
            b["wins"] += 1
        r = (ft.details or {}).get("realized_r")
        if r is not None:
            b["total_r"] += float(r)
            b["r_count"] += 1

    rows = []
    for name, data in sorted(buckets.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = data["wins"] / data["trades"] * 100 if data["trades"] else 0
        avg_r = data["total_r"] / data["r_count"] if data["r_count"] else None
        rows.append(
            {
                "strategy": name,
                "pnl": _safe_round(data["pnl"]),
                "trades": data["trades"],
                "win_rate": _safe_round(wr, 1),
                "avg_r": _safe_round(avg_r) if avg_r is not None else None,
            }
        )
    return rows


# ── Per-Index F&O Summary ─────────────────────────────────────


def _per_index_summary(result: BacktestResult) -> list[dict[str, Any]]:
    """Aggregate F&O PnL per underlying index."""
    from graph_intelligence.agents.strategy_orchestrator import INDEX_FUTURES_SPECS

    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "cost": 0.0}
    )

    for ft in result.fno_trade_log:
        b = buckets[ft.underlying]
        b["pnl"] += ft.pnl
        b["trades"] += 1
        b["cost"] += ft.entry_cost
        if ft.pnl > 0:
            b["wins"] += 1
        else:
            b["losses"] += 1

    rows = []
    for idx, data in sorted(buckets.items(), key=lambda x: x[1]["pnl"], reverse=True):
        spec = INDEX_FUTURES_SPECS.get(idx, {})
        wr = data["wins"] / data["trades"] * 100 if data["trades"] else 0
        roi = data["pnl"] / data["cost"] * 100 if data["cost"] else 0
        rows.append(
            {
                "index": idx,
                "name": spec.get("name", idx),
                "country": spec.get("country", "?"),
                "pnl": _safe_round(data["pnl"]),
                "trades": data["trades"],
                "wins": data["wins"],
                "losses": data["losses"],
                "win_rate": _safe_round(wr, 1),
                "roi_pct": _safe_round(roi, 1),
                "cost": _safe_round(data["cost"]),
            }
        )
    return rows


# ── Per-Asset-Class Attribution ────────────────────────────────


def _per_asset_class_summary(result: BacktestResult) -> list[dict[str, Any]]:
    """Aggregate PnL by asset class across all trades."""
    ASSET_CLASS_MAP = {
        "INDEX_CALL": "Index F&O",
        "INDEX_PUT": "Index F&O",
        "INDEX_SPREAD": "Index F&O",
        "INDEX_FUTURE": "Index F&O",
        "FX": "FX",
        "COMMODITY_FUTURE": "Commodities",
        "CRYPTO": "Crypto",
        "ETF": "ETFs",
        "RATE_FUTURE": "Bonds",
    }
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "cost": 0.0}
    )

    # Equity trades
    for t in result.trades:
        b = buckets["Equities"]
        b["pnl"] += t.pnl
        b["trades"] += 1
        if t.pnl > 0:
            b["wins"] += 1

    # F&O and multi-asset trades
    for ft in result.fno_trade_log:
        if ft.exit_date is None:
            continue
        cls = ASSET_CLASS_MAP.get(ft.instrument, "Other")
        b = buckets[cls]
        b["pnl"] += ft.pnl
        b["trades"] += 1
        b["cost"] += ft.entry_cost
        if ft.pnl > 0:
            b["wins"] += 1

    rows = []
    for cls, data in sorted(buckets.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = data["wins"] / data["trades"] * 100 if data["trades"] else 0
        rows.append(
            {
                "asset_class": cls,
                "pnl": _safe_round(data["pnl"]),
                "trades": data["trades"],
                "wins": data["wins"],
                "win_rate": _safe_round(wr, 1),
                "cost": _safe_round(data["cost"]),
            }
        )
    return rows


# ── Advanced Risk Metrics ─────────────────────────────────────


def _risk_metrics(result: BacktestResult) -> dict[str, Any]:
    """Calmar, Sortino, max consecutive losses, tail risk."""
    eq_curve = [e["equity"] for e in result.equity_curve] if result.equity_curve else []
    if len(eq_curve) < 2:
        return {}

    daily_returns = np.diff(eq_curve) / np.array(eq_curve[:-1])

    sortino = _sortino_ratio(daily_returns)
    calmar = _calmar_ratio(result.annualized_return_pct, result.max_drawdown_pct)

    all_pnls: list[float] = [t.pnl for t in result.trades]
    all_pnls.extend(ft.pnl for ft in result.fno_trade_log if ft.exit_date is not None)
    loss_streak = _max_consecutive([p < 0 for p in all_pnls])
    win_streak = _max_consecutive([p > 0 for p in all_pnls])

    tail_pct = float(np.percentile(daily_returns, 5)) * 100 if len(daily_returns) > 5 else 0.0
    best_day = float(np.max(daily_returns)) * 100 if len(daily_returns) else 0.0
    worst_day = float(np.min(daily_returns)) * 100 if len(daily_returns) else 0.0

    return {
        "sortino_ratio": _safe_round(sortino),
        "calmar_ratio": calmar,
        "max_consecutive_losses": loss_streak,
        "max_consecutive_wins": win_streak,
        "var_5pct_daily": _safe_round(tail_pct, 3),
        "best_day_pct": _safe_round(best_day, 2),
        "worst_day_pct": _safe_round(worst_day, 2),
    }


# ── Alpha Decomposition ──────────────────────────────────────


def _alpha_decomposition(result: BacktestResult) -> dict[str, Any]:
    """Decompose return into equity vs F&O, and graph intelligence contribution."""
    eq_pnl = sum(t.pnl for t in result.trades)
    fno_pnl = sum(ft.pnl for ft in result.fno_trade_log if ft.exit_date is not None)
    total_pnl = eq_pnl + fno_pnl
    init = result.initial_equity

    # Graph intelligence: equity trades with supply_chain rationale OR graph_intel tag
    sc_eq_pnl = sum(
        t.pnl
        for t in result.trades
        if "supply_chain" in (t.signal.rationale or "").lower()
        or "shock" in (t.signal.rationale or "").lower()
    )

    # Graph intelligence: F&O trades tagged graph_intel or shock-directed strategies
    sc_fno_pnl = sum(
        ft.pnl
        for ft in result.fno_trade_log
        if ft.exit_date
        and (
            (ft.details or {}).get("graph_intel", False)
            or "shock_directed" in (ft.strategy or "").lower()
            or "supply_chain" in (ft.strategy or "").lower()
        )
    )
    graph_pnl = sc_eq_pnl + sc_fno_pnl

    gi_trade_count = sum(
        1
        for t in result.trades
        if "supply_chain" in (t.signal.rationale or "").lower()
        or "shock" in (t.signal.rationale or "").lower()
    ) + sum(
        1
        for ft in result.fno_trade_log
        if ft.exit_date
        and (
            (ft.details or {}).get("graph_intel", False)
            or "shock_directed" in (ft.strategy or "").lower()
            or "supply_chain" in (ft.strategy or "").lower()
        )
    )

    return {
        "equity_pnl": _safe_round(eq_pnl),
        "equity_return_pct": _safe_round(eq_pnl / init * 100) if init else 0,
        "fno_pnl": _safe_round(fno_pnl),
        "fno_return_pct": _safe_round(fno_pnl / init * 100) if init else 0,
        "total_pnl": _safe_round(total_pnl),
        "graph_intelligence_pnl": _safe_round(graph_pnl),
        "graph_intelligence_pct": _safe_round(graph_pnl / init * 100) if init else 0,
        "graph_intelligence_trades": gi_trade_count,
        "equity_contribution_pct": _safe_round(eq_pnl / total_pnl * 100, 1) if total_pnl else 0,
        "fno_contribution_pct": _safe_round(fno_pnl / total_pnl * 100, 1) if total_pnl else 0,
    }


# ── R-Multiple Distribution ──────────────────────────────────


def _r_distribution(result: BacktestResult) -> dict[str, Any]:
    """Extract R-multiple distribution from F&O trade details."""
    r_stats = result.extra_stats.get("r_distribution", {})
    fno_log = getattr(result, "fno_trade_log", [])

    realized_rs: list[float] = []
    for ft in fno_log:
        det = ft.details or {}
        r = det.get("realized_r")
        if r is not None:
            realized_rs.append(float(r))

    if realized_rs:
        arr = np.array(realized_rs)
        return {
            "count": len(realized_rs),
            "mean": _safe_round(float(np.mean(arr))),
            "median": _safe_round(float(np.median(arr))),
            "p25": _safe_round(float(np.percentile(arr, 25))),
            "p75": _safe_round(float(np.percentile(arr, 75))),
            "p90": _safe_round(float(np.percentile(arr, 90))),
            "max": _safe_round(float(np.max(arr))),
            "min": _safe_round(float(np.min(arr))),
            "pct_positive": _safe_round(float(np.mean(arr > 0) * 100), 1),
        }

    # No realized R values found — return empty distribution
    fno_count = len([ft for ft in fno_log if ft.exit_date is not None])
    return {
        "count": fno_count,
        "mean": 0,
        "median": 0,
        "p25": 0,
        "p75": 0,
        "p90": 0,
        "max": 0,
        "min": 0,
        "pct_positive": 0,
    }


# ── Executive Summary ────────────────────────────────────────


def _build_executive_summary(result: BacktestResult) -> dict[str, Any]:
    """Core performance metrics."""
    return {
        "period": f"{result.start_date.date()} → {result.end_date.date()}",
        "initial_equity": result.initial_equity,
        "final_equity": _safe_round(result.final_equity),
        "total_return_pct": _safe_round(result.total_return_pct),
        "annualized_return_pct": _safe_round(result.annualized_return_pct),
        "sharpe_ratio": _safe_round(result.sharpe_ratio),
        "max_drawdown_pct": _safe_round(result.max_drawdown_pct),
        "win_rate": _safe_round(result.win_rate),
        "profit_factor": _safe_round(result.profit_factor),
        "total_trades": result.total_trades,
        "fno_trades": result.extra_stats.get("fno_trades", 0),
        "equity_trades": result.extra_stats.get("equity_trades", 0),
        "avg_hold_days": _safe_round(result.avg_hold_days, 1),
    }


def _build_scenario_row(result: BacktestResult) -> dict[str, Any]:
    meta = result.metadata or {}
    return {
        "scenario": meta.get("scenario", "default"),
        "scenario_name": meta.get("scenario_name", "Standard Backtest"),
        "total_return_pct": _safe_round(result.total_return_pct),
        "sharpe_ratio": _safe_round(result.sharpe_ratio),
        "max_drawdown_pct": _safe_round(result.max_drawdown_pct),
        "win_rate": _safe_round(result.win_rate),
        "profit_factor": _safe_round(result.profit_factor),
        "total_trades": result.total_trades,
        "r_distribution": _r_distribution(result),
    }


# ── Backtest TXT / JSON Report ────────────────────────────────


def generate_backtest_report(
    result: BacktestResult,
    output_dir: Path = Path("."),
) -> dict[str, Path]:
    """Generate institutional-grade backtest_report.json and backtest_report.txt.

    Replaces the old raw trade-log dump with structured sections:
    executive summary, strategy attribution, per-index F&O summary,
    risk metrics, alpha decomposition, and R-multiple distribution.
    """
    summary = _build_executive_summary(result)
    strat_attr = _strategy_attribution(result)
    idx_summary = _per_index_summary(result)
    risk = _risk_metrics(result)
    alpha = _alpha_decomposition(result)
    r_dist = _r_distribution(result)

    meta = result.metadata or {}

    report_dict: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "engine": "AYVID AI-Native Quantitative Hedge Fund",
    }
    if meta:
        report_dict["scenario"] = {
            "key": meta.get("scenario", ""),
            "name": meta.get("scenario_name", ""),
            "description": meta.get("scenario_description", ""),
        }

    report_dict["executive_summary"] = summary
    report_dict["risk_metrics"] = risk
    report_dict["alpha_decomposition"] = alpha
    report_dict["r_distribution"] = r_dist
    report_dict["strategy_attribution"] = strat_attr
    report_dict["per_index_fno"] = idx_summary
    report_dict["per_asset_class"] = _per_asset_class_summary(result)

    tx_costs = result.extra_stats.get("transaction_costs", {})
    if tx_costs:
        report_dict["transaction_costs"] = tx_costs

    # Top 10 equity trades by absolute PnL
    eq_trades_sorted = sorted(result.trades, key=lambda t: abs(t.pnl), reverse=True)[:10]
    report_dict["top_equity_trades"] = [
        {
            "ticker": t.signal.ticker,
            "direction": t.signal.action.value,
            "entry_price": _safe_round(t.entry_price),
            "exit_price": _safe_round(t.exit_price) if t.exit_price else None,
            "shares": t.shares,
            "pnl": _safe_round(t.pnl),
            "pnl_pct": _safe_round(t.pnl_pct, 1),
            "hold_days": t.hold_days,
            "strategy": t.signal.rationale[:80] if t.signal.rationale else "",
        }
        for t in eq_trades_sorted
    ]

    # Top 10 F&O trades by absolute PnL
    fno_sorted = sorted(
        [ft for ft in result.fno_trade_log if ft.exit_date is not None],
        key=lambda ft: abs(ft.pnl),
        reverse=True,
    )[:10]
    report_dict["top_fno_trades"] = [_format_fno_trade(ft) for ft in fno_sorted]

    # Monthly equity curve (aggregated)
    monthly: dict[str, float] = {}
    for pt in result.equity_curve:
        month = pt["date"][:7]
        monthly[month] = pt["equity"]
    report_dict["monthly_equity"] = [
        {"month": m, "equity": _safe_round(e)} for m, e in monthly.items()
    ]

    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "backtest_report.json"
    json_path.write_text(json.dumps(report_dict, indent=2, default=str) + "\n")

    txt_path = output_dir / "backtest_report.txt"
    txt_path.write_text(_render_backtest_txt(report_dict))

    try:
        from shared.gcs import sync_artifact
        sync_artifact(json_path)
        sync_artifact(txt_path)
    except Exception:
        pass

    log.info(f"Backtest report: {json_path}, {txt_path}")
    return {"json": json_path, "txt": txt_path}


def _render_backtest_txt(report: dict[str, Any]) -> str:
    """Structured terminal-friendly TXT report."""
    lines: list[str] = []
    w = 70
    lines.append("=" * w)
    lines.append("  AYVID — AI-Native Quantitative Hedge Fund")
    lines.append("  Institutional Backtest Report")
    lines.append(f"  Generated: {report.get('generated_at', '')}")
    lines.append("=" * w)

    sc = report.get("scenario", {})
    if sc and sc.get("name"):
        lines.append(f"\n  Scenario: {sc['name']}")
        lines.append(f"  {sc.get('description', '')}")

    s = report.get("executive_summary", {})
    lines.append(f"\n{'─' * w}")
    lines.append("  EXECUTIVE SUMMARY")
    lines.append(f"{'─' * w}")
    lines.append(f"  Period:              {s.get('period', 'N/A')}")
    lines.append(f"  Initial Equity:      ${s.get('initial_equity', 0):,.0f}")
    lines.append(f"  Final Equity:        ${s.get('final_equity', 0):,.2f}")
    lines.append(f"  Total Return:        {s.get('total_return_pct', 0):+.2f}%")
    lines.append(f"  Annualized Return:   {s.get('annualized_return_pct', 0):+.2f}%")
    lines.append(f"  Sharpe Ratio:        {s.get('sharpe_ratio', 0):.2f}")
    lines.append(f"  Max Drawdown:        {s.get('max_drawdown_pct', 0):.1f}%")
    lines.append(f"  Win Rate:            {s.get('win_rate', 0):.1f}%")
    lines.append(f"  Profit Factor:       {s.get('profit_factor', 0):.2f}")
    lines.append(
        f"  Total Trades:        {s.get('total_trades', 0)} "
        f"(F&O: {s.get('fno_trades', 0)}, Equity: {s.get('equity_trades', 0)})"
    )
    lines.append(f"  Avg Hold Days:       {s.get('avg_hold_days', 0):.1f}")

    rm = report.get("risk_metrics", {})
    if rm:
        lines.append(f"\n{'─' * w}")
        lines.append("  RISK METRICS")
        lines.append(f"{'─' * w}")
        lines.append(f"  Sortino Ratio:       {rm.get('sortino_ratio', 0):.2f}")
        lines.append(f"  Calmar Ratio:        {rm.get('calmar_ratio', 0):.2f}")
        lines.append(f"  Max Consec. Losses:  {rm.get('max_consecutive_losses', 0)}")
        lines.append(f"  Max Consec. Wins:    {rm.get('max_consecutive_wins', 0)}")
        lines.append(f"  VaR (5% daily):      {rm.get('var_5pct_daily', 0):.3f}%")
        lines.append(f"  Best Day:            {rm.get('best_day_pct', 0):+.2f}%")
        lines.append(f"  Worst Day:           {rm.get('worst_day_pct', 0):+.2f}%")

    tx = report.get("transaction_costs", {})
    if tx:
        lines.append(f"\n{'─' * w}")
        lines.append("  TRANSACTION COSTS (post-cost PnL)")
        lines.append(f"{'─' * w}")
        lines.append(f"  Model:               {tx.get('model', 'IBKR Tiered')}")
        lines.append(f"  F&O Commission:      ${tx.get('fno_commission_usd', 0):,.0f}")
        lines.append(f"  F&O Slippage:        ${tx.get('fno_slippage_usd', 0):,.0f}")
        lines.append(f"  F&O Total:           ${tx.get('fno_costs_usd', 0):,.0f}")
        lines.append(f"  Equity Total:        ${tx.get('equity_costs_usd', 0):,.0f}")
        lines.append(f"  All-In Costs:        ${tx.get('total_costs_usd', 0):,.0f}")
        lines.append(f"  Cost Drag:           {tx.get('cost_drag_pct', 0):.3f}% of initial equity")
        lines.append(f"  Round-trips:         {tx.get('round_trip_count', 0)}")

    alpha = report.get("alpha_decomposition", {})
    if alpha:
        lines.append(f"\n{'─' * w}")
        lines.append("  ALPHA DECOMPOSITION")
        lines.append(f"{'─' * w}")
        lines.append(
            f"  Equity PnL:          ${alpha.get('equity_pnl', 0):+,.0f}"
            f"  ({alpha.get('equity_return_pct', 0):+.2f}%)"
        )
        lines.append(
            f"  F&O PnL:             ${alpha.get('fno_pnl', 0):+,.0f}"
            f"  ({alpha.get('fno_return_pct', 0):+.2f}%)"
        )
        lines.append(f"  Total PnL:           ${alpha.get('total_pnl', 0):+,.0f}")
        eq_pct = alpha.get("equity_contribution_pct", 0)
        fno_pct = alpha.get("fno_contribution_pct", 0)
        lines.append(f"  Contribution:        Equity {eq_pct:.0f}% / F&O {fno_pct:.0f}%")
        lines.append(
            f"  Graph Intelligence:  ${alpha.get('graph_intelligence_pnl', 0):+,.0f}"
            f"  ({alpha.get('graph_intelligence_pct', 0):+.2f}%)"
        )

    r = report.get("r_distribution", {})
    if r and r.get("count", 0) > 0:
        lines.append(f"\n{'─' * w}")
        lines.append("  R-MULTIPLE DISTRIBUTION")
        lines.append(f"{'─' * w}")
        lines.append(f"  Trades:              {r.get('count', 0)}")
        lines.append(f"  Mean R:              {r.get('mean', 0):.2f}")
        lines.append(f"  Median R:            {r.get('median', 0):.2f}")
        lines.append(f"  25th / 75th:         {r.get('p25', 0):.2f} / {r.get('p75', 0):.2f}")
        lines.append(f"  % Positive R:        {r.get('pct_positive', 0):.1f}%")

    strats = report.get("strategy_attribution", [])
    if strats:
        lines.append(f"\n{'─' * w}")
        lines.append("  STRATEGY ATTRIBUTION")
        lines.append(f"{'─' * w}")
        lines.append(f"  {'Strategy':<35} {'PnL':>12} {'Trades':>7} {'WR%':>6}")
        lines.append(f"  {'─' * 35} {'─' * 12} {'─' * 7} {'─' * 6}")
        for st in strats:
            name = st["strategy"][:35]
            lines.append(
                f"  {name:<35} ${st['pnl']:>+10,.0f} {st['trades']:>7} {st['win_rate']:>5.0f}%"
            )

    ac_rows = report.get("per_asset_class", [])
    if ac_rows:
        lines.append(f"\n{'─' * w}")
        lines.append("  PER-ASSET-CLASS ATTRIBUTION")
        lines.append(f"{'─' * w}")
        lines.append(f"  {'Asset Class':<20} {'PnL':>12} {'Trades':>7} {'WR%':>6}")
        lines.append(f"  {'─' * 20} {'─' * 12} {'─' * 7} {'─' * 6}")
        for ac in ac_rows:
            lines.append(
                f"  {ac['asset_class']:<20} ${ac['pnl']:>+10,.0f} {ac['trades']:>7} {ac['win_rate']:>5.0f}%"
            )

    idx_rows = report.get("per_index_fno", [])
    if idx_rows:
        lines.append(f"\n{'─' * w}")
        lines.append("  PER-INDEX F&O SUMMARY")
        lines.append(f"{'─' * w}")
        lines.append(f"  {'Index':<12} {'Name':<22} {'PnL':>12} {'W/L':>7} {'WR%':>6}")
        lines.append(f"  {'─' * 12} {'─' * 22} {'─' * 12} {'─' * 7} {'─' * 6}")
        for ix in idx_rows:
            name = ix["name"][:22]
            wl = f"{ix['wins']}/{ix['losses']}"
            lines.append(
                f"  {ix['index']:<12} {name:<22} ${ix['pnl']:>+10,.0f} {wl:>7} {ix['win_rate']:>5.0f}%"
            )

    top_fno = report.get("top_fno_trades", [])
    if top_fno:
        lines.append(f"\n{'─' * w}")
        lines.append("  TOP F&O TRADES (by |PnL|)")
        lines.append(f"{'─' * w}")
        for ft in top_fno:
            mode = ft.get("trade_mode", "INTRADAY")
            r_val = ft.get("realized_r")
            r_str = f"R={r_val:+.2f}" if r_val is not None else ""
            exit_r = ft.get("exit_reason", "")
            conv = ft.get("conviction")
            conv_str = f"conv={conv:.2f}" if conv is not None else ""

            line1 = (
                f"  {ft['underlying']:<10} {ft['instrument']:<14} {ft['direction']:<6} [{mode}]"
            )
            lines.append(line1)

            spot_e = ft.get("spot_entry")
            spot_x = ft.get("spot_exit")
            spot_str = ""
            if spot_e is not None:
                spot_str = f"Spot: {spot_e:,.2f}"
                if spot_x is not None:
                    spot_str += f" → {spot_x:,.2f}"

            inst = ft.get("instrument", "")
            if inst in ("INDEX_CALL", "INDEX_PUT"):
                ot = ft.get("opt_type", "")
                ce_pe = "CE" if ot == "CALL" else "PE"
                strike = ft.get("strike")
                ep = ft.get("entry_premium")
                xp = ft.get("exit_premium")
                dte = ft.get("dte_at_entry")
                iv = ft.get("iv")
                lines.append(
                    f"    {spot_str} | Strike: {strike:,.0f} {ce_pe}"
                    + (f" | IV: {iv:.1%}" if iv else "")
                    + (f" | DTE: {dte}" if dte else "")
                )
                lines.append(
                    f"    Premium: {ep:,.2f} → {xp:,.2f}"
                    + f" | Lots: {ft.get('lots', 1)}"
                    + (f" | Expiry: {ft.get('expiry_date', '')}" if ft.get("expiry_date") else "")
                )
            elif inst == "INDEX_SPREAD":
                ot = ft.get("opt_type", "")
                ce_pe = "CE" if ot == "CALL" else "PE"
                lk = ft.get("long_strike")
                sk = ft.get("short_strike")
                lp = ft.get("long_prem")
                sp = ft.get("short_prem")
                nd = ft.get("net_debit")
                xv = ft.get("exit_spread_val")
                lines.append(
                    f"    {spot_str} | {lk:,.0f}/{sk:,.0f} {ce_pe} Spread"
                    + f" | Width: {ft.get('spread_width', 0):,.0f}"
                )
                lines.append(
                    f"    Debit: {nd:,.2f} (Long: {lp:,.2f}, Short: {sp:,.2f}) → Exit: {xv:,.2f}"
                    + f" | Lots: {ft.get('lots', 1)}"
                )
            elif inst == "INDEX_FUTURE":
                fe = ft.get("fut_entry")
                fx = ft.get("fut_exit")
                pp = ft.get("pnl_pts")
                lines.append(f"    {spot_str} | Fut: {fe:,.2f} → {fx:,.2f} ({pp:+,.2f} pts)")
                lines.append(
                    f"    Lots: {ft.get('lots', 1)} | Margin/lot: ${ft.get('margin_per_lot', 0):,.0f}"
                )

            risk_usd = ft.get("risk_1R_usd")
            risk_str = f"1R=${risk_usd:,.0f}" if risk_usd else ""
            lines.append(
                f"    PnL: ${ft['pnl']:>+,.0f} ({ft['pnl_pct']:+.1f}%)"
                + f" | {r_str} | {risk_str} | {exit_r} | {conv_str}"
            )
            lines.append("")

    top_eq = report.get("top_equity_trades", [])
    if top_eq:
        lines.append(f"{'─' * w}")
        lines.append("  TOP EQUITY TRADES (by |PnL|)")
        lines.append(f"{'─' * w}")
        for t in top_eq:
            direction = t.get("direction", "BUY")
            entry_p = t.get("entry_price")
            exit_p = t.get("exit_price")
            shares = t.get("shares")
            price_str = ""
            if entry_p is not None:
                price_str = f"Entry: ${entry_p:,.2f}"
                if exit_p is not None:
                    price_str += f" → ${exit_p:,.2f}"
            shares_str = f" | Shares: {shares}" if shares else ""
            lines.append(f"  {t['ticker']:<10} {direction:<6}" + f" | {price_str}{shares_str}")
            lines.append(
                f"    PnL: ${t['pnl']:>+,.0f} ({t['pnl_pct']:+.1f}%)"
                + f" | {t['hold_days']}d | {t['strategy'][:50]}"
            )
            lines.append("")

    lines.append(f"{'=' * w}")
    lines.append("")
    return "\n".join(lines)


# ── YC Performance Report (multi-scenario) ────────────────────


def generate_yc_report(
    results: list[BacktestResult],
    baseline_results: list[BacktestResult] | None = None,
    buy_hold_benchmarks: list[dict[str, Any]] | None = None,
    output_dir: Path = Path("."),
) -> dict[str, Path]:
    """Generate a YC-ready multi-scenario performance report.

    Args:
        results: BacktestResult objects from AI-native runs.
        baseline_results: Optional baseline runs for alpha delta.
        buy_hold_benchmarks: Buy-and-hold benchmark data per scenario.
        output_dir: Where to write output files.
    """
    now_str = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")

    report: dict[str, Any] = {
        "generated_at": now_str,
        "engine": "AYVID AI-Native Quantitative Hedge Fund",
        "version": "2.0",
    }

    if len(results) == 1:
        report["executive_summary"] = _build_executive_summary(results[0])
        report["risk_metrics"] = _risk_metrics(results[0])
        report["alpha_decomposition"] = _alpha_decomposition(results[0])
        report["r_distribution"] = _r_distribution(results[0])
        report["strategy_attribution"] = _strategy_attribution(results[0])
        report["per_index_fno"] = _per_index_summary(results[0])
    else:
        report["per_scenario"] = [_build_scenario_row(r) for r in results]

        # Aggregate executive summary across all scenarios
        all_trades = sum(r.total_trades for r in results)
        all_wins = sum(
            sum(1 for t in r.trades if t.pnl > 0)
            + sum(1 for ft in r.fno_trade_log if ft.exit_date and ft.pnl > 0)
            for r in results
        )
        all_gross_profit = sum(
            sum(t.pnl for t in r.trades if t.pnl > 0)
            + sum(ft.pnl for ft in r.fno_trade_log if ft.exit_date and ft.pnl > 0)
            for r in results
        )
        all_gross_loss = abs(
            sum(
                sum(t.pnl for t in r.trades if t.pnl < 0)
                + sum(ft.pnl for ft in r.fno_trade_log if ft.exit_date and ft.pnl < 0)
                for r in results
            )
        )

        sharpes = [r.sharpe_ratio for r in results]
        returns = [r.total_return_pct for r in results]
        dds = [r.max_drawdown_pct for r in results]
        fno_counts = [r.extra_stats.get("fno_trades", 0) for r in results]
        eq_counts = [r.extra_stats.get("equity_trades", 0) for r in results]
        earliest = min(r.start_date for r in results)
        latest = max(r.end_date for r in results)

        report["executive_summary"] = {
            "period": f"{earliest.date()} → {latest.date()} ({len(results)} scenarios)",
            "initial_equity": results[0].initial_equity,
            "final_equity": _safe_round(float(np.mean([r.final_equity for r in results]))),
            "total_return_pct": _safe_round(float(np.mean(returns))),
            "annualized_return_pct": _safe_round(
                float(np.mean(returns))
                * 365.25
                / max(float(np.mean([(r.end_date - r.start_date).days for r in results])), 1)
            ),
            "sharpe_ratio": _safe_round(float(np.mean(sharpes))),
            "max_drawdown_pct": _safe_round(float(np.max(dds))),
            "win_rate": _safe_round(all_wins / all_trades * 100 if all_trades else 0),
            "profit_factor": _safe_round(
                all_gross_profit / all_gross_loss if all_gross_loss > 0 else 0
            ),
            "total_trades": all_trades,
            "fno_trades": sum(fno_counts),
            "equity_trades": sum(eq_counts),
            "avg_hold_days": _safe_round(float(np.mean([r.avg_hold_days for r in results])), 1),
        }

        # Aggregate risk: use worst-case across scenarios
        risk_list = [_risk_metrics(r) for r in results]
        risk_list = [rm for rm in risk_list if rm]
        if risk_list:
            report["risk_metrics"] = {
                "sortino_ratio": _safe_round(
                    float(np.mean([rm.get("sortino_ratio", 0) for rm in risk_list]))
                ),
                "calmar_ratio": _safe_round(
                    float(np.mean([rm.get("calmar_ratio", 0) for rm in risk_list]))
                ),
                "max_consecutive_losses": max(
                    rm.get("max_consecutive_losses", 0) for rm in risk_list
                ),
                "max_consecutive_wins": max(rm.get("max_consecutive_wins", 0) for rm in risk_list),
                "var_5pct_daily": _safe_round(
                    float(np.min([rm.get("var_5pct_daily", 0) for rm in risk_list])), 3
                ),
                "best_day_pct": _safe_round(
                    float(np.max([rm.get("best_day_pct", 0) for rm in risk_list])), 2
                ),
                "worst_day_pct": _safe_round(
                    float(np.min([rm.get("worst_day_pct", 0) for rm in risk_list])), 2
                ),
            }
        else:
            report["risk_metrics"] = {}

        # Aggregate alpha decomposition: sum across scenarios
        alpha_list = [_alpha_decomposition(r) for r in results]
        total_eq_pnl = sum(a["equity_pnl"] for a in alpha_list)
        total_fno_pnl = sum(a["fno_pnl"] for a in alpha_list)
        total_pnl = total_eq_pnl + total_fno_pnl
        total_gi_pnl = sum(a["graph_intelligence_pnl"] for a in alpha_list)
        total_gi_trades = sum(a.get("graph_intelligence_trades", 0) for a in alpha_list)
        init = results[0].initial_equity
        report["alpha_decomposition"] = {
            "equity_pnl": _safe_round(total_eq_pnl),
            "equity_return_pct": _safe_round(total_eq_pnl / init * 100) if init else 0,
            "fno_pnl": _safe_round(total_fno_pnl),
            "fno_return_pct": _safe_round(total_fno_pnl / init * 100) if init else 0,
            "total_pnl": _safe_round(total_pnl),
            "graph_intelligence_pnl": _safe_round(total_gi_pnl),
            "graph_intelligence_pct": _safe_round(total_gi_pnl / init * 100) if init else 0,
            "graph_intelligence_trades": total_gi_trades,
            "equity_contribution_pct": _safe_round(total_eq_pnl / total_pnl * 100, 1)
            if total_pnl
            else 0,
            "fno_contribution_pct": _safe_round(total_fno_pnl / total_pnl * 100, 1)
            if total_pnl
            else 0,
        }

        # Aggregate R distribution
        all_rs: list[float] = []
        for r in results:
            for ft in r.fno_trade_log:
                rv = (ft.details or {}).get("realized_r")
                if rv is not None:
                    all_rs.append(float(rv))
        if all_rs:
            arr = np.array(all_rs)
            report["r_distribution"] = {
                "count": len(all_rs),
                "mean": _safe_round(float(np.mean(arr))),
                "median": _safe_round(float(np.median(arr))),
                "p25": _safe_round(float(np.percentile(arr, 25))),
                "p75": _safe_round(float(np.percentile(arr, 75))),
                "p90": _safe_round(float(np.percentile(arr, 90))),
                "max": _safe_round(float(np.max(arr))),
                "min": _safe_round(float(np.min(arr))),
                "pct_positive": _safe_round(float(np.mean(arr > 0) * 100), 1),
            }
        else:
            report["r_distribution"] = _r_distribution(results[0])

        # Merge strategy attribution across scenarios
        merged_strats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"pnl": 0.0, "trades": 0, "wins": 0}
        )
        for r in results:
            for st in _strategy_attribution(r):
                b = merged_strats[st["strategy"]]
                b["pnl"] += st["pnl"]
                b["trades"] += st["trades"]
                b["wins"] += int(st["win_rate"] * st["trades"] / 100) if st["trades"] else 0
        report["strategy_attribution"] = sorted(
            [
                {
                    "strategy": name,
                    "pnl": _safe_round(d["pnl"]),
                    "trades": d["trades"],
                    "win_rate": _safe_round(d["wins"] / d["trades"] * 100, 1)
                    if d["trades"]
                    else 0,
                    "avg_r": None,
                }
                for name, d in merged_strats.items()
            ],
            key=lambda x: x["pnl"],
            reverse=True,
        )

        # Merge per-index summary across scenarios
        merged_idx: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "cost": 0.0}
        )
        for r in results:
            for ix in _per_index_summary(r):
                b = merged_idx[ix["index"]]
                b["pnl"] += ix["pnl"]
                b["trades"] += ix["trades"]
                b["wins"] += ix["wins"]
                b["losses"] += ix["losses"]
                b["cost"] += ix.get("cost", 0.0)
                b["name"] = ix["name"]
                b["country"] = ix["country"]
        report["per_index_fno"] = sorted(
            [
                {
                    "index": idx,
                    "name": d.get("name", idx),
                    "country": d.get("country", "?"),
                    "pnl": _safe_round(d["pnl"]),
                    "trades": d["trades"],
                    "wins": d["wins"],
                    "losses": d["losses"],
                    "win_rate": _safe_round(d["wins"] / d["trades"] * 100, 1)
                    if d["trades"]
                    else 0,
                    "roi_pct": _safe_round(d["pnl"] / d["cost"] * 100, 1)
                    if d.get("cost", 0) > 0
                    else 0,
                }
                for idx, d in merged_idx.items()
            ],
            key=lambda x: x["pnl"],
            reverse=True,
        )

        report["aggregate"] = {
            "num_scenarios": len(results),
            "avg_sharpe": _safe_round(float(np.mean(sharpes))),
            "avg_return_pct": _safe_round(float(np.mean(returns))),
            "worst_return_pct": _safe_round(float(np.min(returns))),
            "best_return_pct": _safe_round(float(np.max(returns))),
            "min_sharpe": _safe_round(float(np.min(sharpes))),
            "max_sharpe": _safe_round(float(np.max(sharpes))),
            "worst_drawdown_pct": _safe_round(float(np.max(dds))),
            "total_trades": all_trades,
            "pooled_win_rate": _safe_round(all_wins / all_trades * 100 if all_trades else 0),
            "pooled_profit_factor": _safe_round(
                all_gross_profit / all_gross_loss if all_gross_loss > 0 else 0
            ),
        }

    if baseline_results:
        deltas = []
        for ai, base in zip(results, baseline_results):
            delta = {
                "scenario": (ai.metadata or {}).get("scenario", "default"),
                "ai_return_pct": _safe_round(ai.total_return_pct),
                "baseline_return_pct": _safe_round(base.total_return_pct),
                "alpha_delta_pct": _safe_round(ai.total_return_pct - base.total_return_pct),
                "ai_sharpe": _safe_round(ai.sharpe_ratio),
                "baseline_sharpe": _safe_round(base.sharpe_ratio),
                "sharpe_delta": _safe_round(ai.sharpe_ratio - base.sharpe_ratio),
                "ai_max_dd": _safe_round(ai.max_drawdown_pct),
                "baseline_max_dd": _safe_round(base.max_drawdown_pct),
            }
            deltas.append(delta)

        avg_alpha = float(np.mean([d["alpha_delta_pct"] for d in deltas]))
        avg_sharpe_delta = float(np.mean([d["sharpe_delta"] for d in deltas]))
        report["ai_vs_baseline"] = {
            "per_scenario": deltas,
            "avg_alpha_delta_pct": _safe_round(avg_alpha),
            "avg_sharpe_delta": _safe_round(avg_sharpe_delta),
            "conclusion": (
                f"AI-native strategy outperforms baseline by {avg_alpha:+.2f}% return "
                f"and {avg_sharpe_delta:+.2f} Sharpe on average across "
                f"{len(deltas)} scenarios."
            ),
        }

    if buy_hold_benchmarks:
        bh_deltas = []
        for i, bh in enumerate(buy_hold_benchmarks):
            if i < len(results):
                ai_ret = results[i].total_return_pct
                bh_ret = bh.get("portfolio_return_pct", 0.0)
                ai_dd = results[i].max_drawdown_pct
                bh_dd = bh.get("avg_max_drawdown_pct", 0.0)
                meta = results[i].metadata or {}
                bh_deltas.append(
                    {
                        "scenario": meta.get("scenario", f"scenario_{i}"),
                        "scenario_name": meta.get("scenario_name", ""),
                        "ayvid_return_pct": _safe_round(ai_ret),
                        "buy_hold_return_pct": _safe_round(bh_ret),
                        "alpha_pct": _safe_round(ai_ret - bh_ret),
                        "ayvid_max_dd_pct": _safe_round(ai_dd),
                        "buy_hold_max_dd_pct": _safe_round(bh_dd),
                        "tickers_held": bh.get("count", 0),
                    }
                )
        if bh_deltas:
            avg_alpha = float(np.mean([d["alpha_pct"] for d in bh_deltas]))
            report["vs_buy_and_hold"] = {
                "per_scenario": bh_deltas,
                "avg_alpha_pct": _safe_round(avg_alpha),
                "conclusion": (
                    f"AYVID generated {avg_alpha:+.2f}% alpha over equal-weight "
                    f"buy-and-hold across {len(bh_deltas)} historical shock scenarios."
                ),
            }

    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "yc_performance_report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    md_path = output_dir / "yc_performance_report.md"
    md_path.write_text(_render_markdown(report))

    try:
        from shared.gcs import sync_artifact
        sync_artifact(json_path)
        sync_artifact(md_path)
    except Exception:
        pass

    log.info(f"YC report generated: {json_path}, {md_path}")
    return {"json": json_path, "markdown": md_path}


def _render_markdown(report: dict[str, Any]) -> str:
    """Render the report dict as a human-readable Markdown document."""
    lines: list[str] = []
    lines.append("# AYVID Performance Report")
    lines.append("")
    lines.append(f"*Generated: {report['generated_at']}*")
    lines.append("")

    summary = report.get("executive_summary", {})
    if summary:
        lines.append("## Executive Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Period | {summary.get('period', 'N/A')} |")
        lines.append(f"| Initial Equity | ${summary.get('initial_equity', 0):,.0f} |")
        lines.append(f"| Final Equity | ${summary.get('final_equity', 0):,.0f} |")
        lines.append(f"| Total Return | {summary.get('total_return_pct', 0):+.2f}% |")
        lines.append(f"| Annualized Return | {summary.get('annualized_return_pct', 0):+.2f}% |")
        lines.append(f"| Sharpe Ratio | {summary.get('sharpe_ratio', 0):.2f} |")
        lines.append(f"| Max Drawdown | {summary.get('max_drawdown_pct', 0):.1f}% |")
        lines.append(f"| Win Rate | {summary.get('win_rate', 0):.0f}% |")
        lines.append(f"| Profit Factor | {summary.get('profit_factor', 0):.2f} |")
        lines.append(
            f"| Total Trades | {summary.get('total_trades', 0)} (F&O: {summary.get('fno_trades', 0)}, Equity: {summary.get('equity_trades', 0)}) |"
        )
        lines.append("")

    tx = report.get("transaction_costs", {})
    if tx:
        lines.append("## Transaction Costs (post-cost)")
        lines.append("")
        lines.append("| Item | Value |")
        lines.append("|------|-------|")
        lines.append(f"| Model | {tx.get('model', 'IBKR Tiered')} |")
        lines.append(f"| F&O Commission | ${tx.get('fno_commission_usd', 0):,.0f} |")
        lines.append(f"| F&O Slippage | ${tx.get('fno_slippage_usd', 0):,.0f} |")
        lines.append(f"| Equity Costs | ${tx.get('equity_costs_usd', 0):,.0f} |")
        lines.append(f"| **All-In Costs** | **${tx.get('total_costs_usd', 0):,.0f}** |")
        lines.append(f"| Cost Drag | {tx.get('cost_drag_pct', 0):.3f}% of initial equity |")
        lines.append(f"| Round-trips | {tx.get('round_trip_count', 0)} |")
        lines.append("")
        lines.append(
            "> All Sharpe, return, win-rate, and R-multiple metrics in this report are net of these costs."
        )
        lines.append("")

    rm = report.get("risk_metrics", {})
    if rm:
        lines.append("## Risk Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Sortino Ratio | {rm.get('sortino_ratio', 0):.2f} |")
        lines.append(f"| Calmar Ratio | {rm.get('calmar_ratio', 0):.2f} |")
        lines.append(f"| Max Consecutive Losses | {rm.get('max_consecutive_losses', 0)} |")
        lines.append(f"| Max Consecutive Wins | {rm.get('max_consecutive_wins', 0)} |")
        lines.append(f"| VaR (5%, daily) | {rm.get('var_5pct_daily', 0):.3f}% |")
        lines.append(f"| Best Day | {rm.get('best_day_pct', 0):+.2f}% |")
        lines.append(f"| Worst Day | {rm.get('worst_day_pct', 0):+.2f}% |")
        lines.append("")

    alpha = report.get("alpha_decomposition", {})
    if alpha:
        lines.append("## Alpha Decomposition")
        lines.append("")
        lines.append("| Source | PnL | Return | Contribution |")
        lines.append("|--------|-----|--------|-------------|")
        lines.append(
            f"| Equity | ${alpha.get('equity_pnl', 0):+,.0f} | {alpha.get('equity_return_pct', 0):+.2f}% | {alpha.get('equity_contribution_pct', 0):.0f}% |"
        )
        lines.append(
            f"| F&O | ${alpha.get('fno_pnl', 0):+,.0f} | {alpha.get('fno_return_pct', 0):+.2f}% | {alpha.get('fno_contribution_pct', 0):.0f}% |"
        )
        gi_trades = alpha.get("graph_intelligence_trades", 0)
        lines.append(
            f"| **Graph Intelligence** | **${alpha.get('graph_intelligence_pnl', 0):+,.0f}** | **{alpha.get('graph_intelligence_pct', 0):+.2f}%** | {gi_trades} trades |"
        )
        lines.append("")

    r_dist = report.get("r_distribution", {})
    if r_dist and r_dist.get("count", 0) > 0:
        lines.append("## R-Multiple Distribution")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Count | {r_dist.get('count', 0)} |")
        lines.append(f"| Mean R | {r_dist.get('mean', 0):.2f} |")
        lines.append(f"| Median R | {r_dist.get('median', 0):.2f} |")
        lines.append(f"| 25th / 75th | {r_dist.get('p25', 0):.2f} / {r_dist.get('p75', 0):.2f} |")
        lines.append(f"| % Positive | {r_dist.get('pct_positive', 0):.1f}% |")
        lines.append("")

    strats = report.get("strategy_attribution", [])
    if strats:
        lines.append("## Strategy Attribution")
        lines.append("")
        lines.append("| Strategy | PnL | Trades | Win Rate |")
        lines.append("|----------|-----|--------|----------|")
        for st in strats:
            lines.append(
                f"| {st['strategy']} | ${st['pnl']:+,.0f} | {st['trades']} | {st['win_rate']:.0f}% |"
            )
        lines.append("")

    idx_rows = report.get("per_index_fno", [])
    if idx_rows:
        lines.append("## Per-Index F&O Summary")
        lines.append("")
        lines.append("| Index | Name | Country | PnL | W/L | Win Rate |")
        lines.append("|-------|------|---------|-----|-----|----------|")
        for ix in idx_rows:
            lines.append(
                f"| {ix['index']} | {ix['name']} | {ix['country']} | ${ix['pnl']:+,.0f} | {ix['wins']}/{ix['losses']} | {ix['win_rate']:.0f}% |"
            )
        lines.append("")

    agg = report.get("aggregate", {})
    if agg:
        lines.append("## Aggregate Performance (All Scenarios)")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Scenarios | {agg.get('num_scenarios', 0)} |")
        lines.append(f"| Avg Return | {agg.get('avg_return_pct', 0):+.2f}% |")
        lines.append(f"| Best Return | {agg.get('best_return_pct', 0):+.2f}% |")
        lines.append(f"| Worst Return | {agg.get('worst_return_pct', 0):+.2f}% |")
        lines.append(f"| Avg Sharpe | {agg.get('avg_sharpe', 0):.2f} |")
        lines.append(
            f"| Min/Max Sharpe | {agg.get('min_sharpe', 0):.2f} / {agg.get('max_sharpe', 0):.2f} |"
        )
        lines.append(f"| Worst Drawdown | {agg.get('worst_drawdown_pct', 0):.1f}% |")
        lines.append(f"| Total Trades | {agg.get('total_trades', 0)} |")
        lines.append(f"| Pooled Win Rate | {agg.get('pooled_win_rate', 0):.1f}% |")
        lines.append(f"| Pooled Profit Factor | {agg.get('pooled_profit_factor', 0):.2f} |")
        lines.append("")

    scenarios = report.get("per_scenario", [])
    if scenarios:
        lines.append("## Per-Scenario Breakdown")
        lines.append("")
        lines.append("| Scenario | Return% | Sharpe | Max DD% | Win Rate | Trades |")
        lines.append("|----------|---------|--------|---------|----------|--------|")
        for sc in scenarios:
            lines.append(
                f"| {sc.get('scenario_name', 'N/A')} "
                f"| {sc.get('total_return_pct', 0):+.2f}% "
                f"| {sc.get('sharpe_ratio', 0):.2f} "
                f"| {sc.get('max_drawdown_pct', 0):.1f}% "
                f"| {sc.get('win_rate', 0):.0f}% "
                f"| {sc.get('total_trades', 0)} |"
            )
        lines.append("")

    comparison = report.get("ai_vs_baseline", {})
    if comparison:
        lines.append("## AI-Native vs Baseline")
        lines.append("")
        lines.append("| Scenario | AI Return | Baseline | Alpha | AI Sharpe | Base Sharpe |")
        lines.append("|----------|-----------|----------|-------|-----------|-------------|")
        for d in comparison.get("per_scenario", []):
            lines.append(
                f"| {d.get('scenario', 'N/A')} "
                f"| {d.get('ai_return_pct', 0):+.2f}% "
                f"| {d.get('baseline_return_pct', 0):+.2f}% "
                f"| **{d.get('alpha_delta_pct', 0):+.2f}%** "
                f"| {d.get('ai_sharpe', 0):.2f} "
                f"| {d.get('baseline_sharpe', 0):.2f} |"
            )
        lines.append("")
        lines.append(f"> {comparison.get('conclusion', '')}")
        lines.append("")

    bh = report.get("vs_buy_and_hold", {})
    if bh:
        lines.append("## AYVID vs Buy-and-Hold")
        lines.append("")
        lines.append("| Scenario | AYVID | Buy & Hold | Alpha | AYVID DD | B&H DD |")
        lines.append("|----------|-------|------------|-------|----------|--------|")
        for d in bh.get("per_scenario", []):
            lines.append(
                f"| {d.get('scenario_name') or d.get('scenario', 'N/A')} "
                f"| {d.get('ayvid_return_pct', 0):+.2f}% "
                f"| {d.get('buy_hold_return_pct', 0):+.2f}% "
                f"| **{d.get('alpha_pct', 0):+.2f}%** "
                f"| {d.get('ayvid_max_dd_pct', 0):.1f}% "
                f"| {d.get('buy_hold_max_dd_pct', 0):.1f}% |"
            )
        lines.append("")
        lines.append(f"> {bh.get('conclusion', '')}")
        lines.append("")

    lines.append("---")
    lines.append(f"*{report.get('engine', 'AYVID')} — v{report.get('version', '2.0')}*")
    lines.append("")
    return "\n".join(lines)


# ── Strategy Win Rate Report ────────────────────────────────


_STRATEGY_TYPE_MAP: dict[str, str] = {
    "shock_directed": "shock_driven",
    "supply_chain_beneficiary": "shock_driven",
    "supply_chain_oversold_dip": "shock_driven",
    "supply_chain_hc": "shock_driven",
    "premium_iron_condor": "premium",
    "premium_credit_spread": "premium",
    "premium_cash_secured_put": "premium",
    "momentum_equity_long": "directional",
    "quality_growth_long": "directional",
    "earnings_beat_momentum": "directional",
    "earnings_drift_long": "directional",
    "positive_shock_beneficiary": "directional",
    "deep_value_investment": "directional",
    "trend_investment": "directional",
    "mr_equity_bounce": "directional",
    "vix_mean_reversion": "directional",
}


def classify_strategy_type(strategy_name: str) -> str:
    """Classify a strategy name into its type tier."""
    for prefix, stype in _STRATEGY_TYPE_MAP.items():
        if strategy_name.startswith(prefix):
            return stype
    if strategy_name.startswith("idx_"):
        return "directional"
    if strategy_name.startswith("crisis_"):
        return "shock_driven"
    return "directional"


def strategy_win_rate_report(result: BacktestResult) -> dict[str, Any]:
    """Produce a per-strategy win rate report sorted by win rate.

    Returns a dict with:
    - strategies: list of per-strategy rows
    - blended_win_rate: overall
    - premium_win_rate, directional_win_rate, shock_win_rate: per-tier
    """
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "total_r": 0.0, "r_count": 0}
    )

    # F&O trades
    for ft in result.fno_trade_log:
        name = ft.strategy or "fno_other"
        b = buckets[name]
        b["trades"] += 1
        if ft.pnl > 0:
            b["wins"] += 1
        b["pnl"] += ft.pnl
        r = (ft.details or {}).get("realized_r")
        if r is not None:
            b["total_r"] += float(r)
            b["r_count"] += 1

    # Equity trades
    for trade in result.trades:
        name = getattr(trade.signal, "strategy_name", "equity_other") if hasattr(trade, "signal") else "equity_other"
        b = buckets[name]
        b["trades"] += 1
        if trade.pnl > 0:
            b["wins"] += 1
        b["pnl"] += trade.pnl

    # Build per-strategy rows
    rows = []
    tier_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"trades": 0, "wins": 0})
    for name, data in sorted(buckets.items(), key=lambda x: (-x[1]["wins"] / max(x[1]["trades"], 1), -x[1]["pnl"])):
        wr = data["wins"] / data["trades"] * 100 if data["trades"] else 0.0
        avg_r = data["total_r"] / data["r_count"] if data["r_count"] else None
        stype = classify_strategy_type(name)
        tier_totals[stype]["trades"] += data["trades"]
        tier_totals[stype]["wins"] += data["wins"]
        rows.append({
            "strategy": name,
            "type": stype,
            "win_rate": round(wr, 1),
            "mean_r": round(avg_r, 2) if avg_r else None,
            "trades": data["trades"],
            "pnl": round(data["pnl"], 2),
        })

    total_trades = sum(d["trades"] for d in buckets.values())
    total_wins = sum(d["wins"] for d in buckets.values())

    def _tier_wr(tier: str) -> float:
        t = tier_totals.get(tier, {"trades": 0, "wins": 0})
        return round(t["wins"] / max(t["trades"], 1) * 100, 1)

    return {
        "strategies": rows,
        "blended_win_rate": round(total_wins / max(total_trades, 1) * 100, 1),
        "premium_win_rate": _tier_wr("premium"),
        "directional_win_rate": _tier_wr("directional"),
        "shock_win_rate": _tier_wr("shock_driven"),
        "total_trades": total_trades,
    }

