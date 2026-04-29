"""Empirical calibration harness for AYVID strategy parameters.

Runs a grid sweep across the historical scenario catalog, tags every
executed trade with the `(strategy, regime)` cell it was entered in,
then for each cell picks the parameter combination that maximises
trade-level Sharpe (with a sample-size floor and confidence interval).

The output is `calibration/priors.json` — a statistically-validated
prior that the LLM Supervisor reads at decision time. From that point
forward the LLM acts as a Bayesian *update operator* over known-good
anchors, not a free-form generator.

CLI
───
    # ~8 minutes — validates the pipeline end-to-end.
    ayvid data calibrate --mode quick

    # ~30 minutes — useful daily-cron refresh.
    ayvid data calibrate --mode medium

    # Hours — full grid; run overnight.
    ayvid data calibrate --mode full --concurrency 6

Design choices
──────────────
- **Async parallelism** — the inner loop runs `run_scenario_backtest`
  tasks inside an `asyncio.Semaphore`. Each scenario is numpy-heavy but
  stops the event loop only briefly between days, so moderate
  concurrency (3–8) yields a linear speedup.
- **Post-hoc regime tagging** — we don't need to mutate the engine.
  After a scenario run, each trade's (ticker, entry_date) is re-fed
  through `classify_*` using cached OHLCV, and the (trend × vol)
  bucket is recorded. This keeps the engine clean and backwards
  compatible.
- **Per-cell statistical gate** — cells with n<5 trades are marked as
  insufficient and the hardcoded fallback is preserved. Cells with
  n≥5 carry a 95% Sharpe confidence interval (normal approximation;
  bootstrap is future work).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import itertools
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from backtesting.engine import (
    SHOCK_SCENARIOS,
    BacktestResult,
    BacktestTrade,
    _load_index_bars,
    _load_local_bars,
    run_scenario_backtest,
)
from execution_engine.math.regime import (
    classify_trend,
    classify_volatility,
)
from execution_engine.math.signals import compute_hurst_exponent
from shared.logger import get_logger

log = get_logger("calibrate")


# ═══════════════════════════════════════════════════════════════════
#  Parameter grid
# ═══════════════════════════════════════════════════════════════════

# Full grid (matches user spec). 5×4×4×5 = 400 configs.
FULL_GRID: dict[str, list[float]] = {
    "max_rsi_entry_long": [15.0, 20.0, 25.0, 30.0, 35.0],
    "max_z_entry_long": [-2.5, -2.0, -1.5, -1.0],
    "min_factor_score": [0.25, 0.35, 0.45, 0.55],
    "min_expected_r": [1.2, 1.5, 2.0, 2.5, 3.0],
}

# Medium-resolution grid — 3×2×2×3 = 36 configs — weekly-refresh friendly.
MEDIUM_GRID: dict[str, list[float]] = {
    "max_rsi_entry_long": [20.0, 25.0, 32.0],
    "max_z_entry_long": [-2.0, -1.2],
    "min_factor_score": [0.30, 0.45],
    "min_expected_r": [1.2, 1.8, 2.5],
}

# Smoke grid — 2×2×2×2 = 16 configs — fast CI validation.
QUICK_GRID: dict[str, list[float]] = {
    "max_rsi_entry_long": [22.0, 32.0],
    "max_z_entry_long": [-2.0, -1.2],
    "min_factor_score": [0.30, 0.45],
    "min_expected_r": [1.5, 2.5],
}

GRID_PRESETS: dict[str, dict[str, list[float]]] = {
    "quick": QUICK_GRID,
    "medium": MEDIUM_GRID,
    "full": FULL_GRID,
}

# Minimum trades per (strategy, regime) cell to trust the Sharpe estimate.
# Below this threshold the cell emits "insufficient_samples" and callers
# fall back to hardcoded defaults.
MIN_CELL_SAMPLES = 5


# ═══════════════════════════════════════════════════════════════════
#  Regime bucketing
# ═══════════════════════════════════════════════════════════════════


def regime_key(
    trend_regime: str,
    vol_regime: str,
    ret_20d: float,
) -> str:
    """Compose the priors.json regime bucket string.

    Format examples:
        STRONG_TREND_UP__HIGH_VOL
        STRONG_TREND_DOWN__CRISIS
        MEAN_REVERTING__NORMAL
        CHOPPY__LOW_VOL

    Direction suffix (_UP / _DOWN) is only attached when the trend
    regime is STRONG_TREND or WEAK_TREND — mean-reverting or choppy
    markets are direction-agnostic for the prior.
    """
    if trend_regime in ("STRONG_TREND", "WEAK_TREND"):
        direction = "_UP" if ret_20d > 0 else "_DOWN"
        return f"{trend_regime}{direction}__{vol_regime}"
    return f"{trend_regime}__{vol_regime}"


# In-process cache for OHLCV bars used during regime tagging. The
# engine auto-fetches historical data for each backtest run but does
# not persist it to data_store/, so the calibration harness may need
# to fetch again on its own. We cache aggressively here to keep the
# network footprint tiny.
_bars_cache: dict[str, list[Any]] = {}


async def _fetch_historical_bars_async(
    ticker: str,
    is_index: bool,
    entry_date: dt.date,
) -> list[Any]:
    """Fetch 120 days of history ending at or before `entry_date`.

    This is the fallback used when `_load_*_bars` returns no local
    data for the historical window of a calibration scenario (e.g.,
    `data_store/ohlcv/AAPL.jsonl` only covers 2025-10 onward but the
    covid_crash_2020 scenario needs Feb 2020 history).
    """
    try:
        from data_primitives.extractors.openbb_client import fetch_ohlcv

        end = entry_date + dt.timedelta(days=1)
        start = entry_date - dt.timedelta(days=180)
        bars = await fetch_ohlcv(
            ticker,
            days=180,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        return list(bars) if bars else []
    except Exception as exc:
        log.debug(f"historical fetch failed for {ticker} @ {entry_date}: {exc}")
        return []


async def _cached_bars_async(
    ticker: str,
    is_index: bool,
    entry_date: dt.date,
) -> list[Any]:
    """Async cached-bars accessor with network fallback.

    Hot-path: local data_store hit. Cold path: single network fetch
    that populates the cache for the rest of the sweep.
    """
    key = f"{'idx' if is_index else 'eq'}:{ticker}"
    if key in _bars_cache:
        bars = _bars_cache[key]
        filt = [b for b in bars if b.timestamp.date() <= entry_date]
        if len(filt) >= 30:
            return bars
        _bars_cache.pop(key, None)

    local = _load_index_bars(ticker) if is_index else _load_local_bars(ticker)
    filt = [b for b in local if b.timestamp.date() <= entry_date]
    if len(filt) >= 30:
        _bars_cache[key] = local
        return local

    fetched = await _fetch_historical_bars_async(ticker, is_index, entry_date)
    merged = _merge_bars(local, fetched)
    _bars_cache[key] = merged
    return merged


def _merge_bars(a: list[Any], b: list[Any]) -> list[Any]:
    """Union two bar lists, deduplicating by timestamp. Sorted ascending."""
    by_ts: dict[dt.datetime, Any] = {}
    for lst in (a, b):
        for bar in lst or []:
            by_ts[bar.timestamp] = bar
    return [by_ts[k] for k in sorted(by_ts.keys())]


async def regime_at_entry_async(
    ticker: str,
    entry_date: dt.date,
    is_index: bool,
) -> str:
    """Reconstruct the regime a trade was entered into (async version).

    Returns `"UNKNOWN"` when even the network fetch can't find enough
    bars to classify. Callers drop those rows before aggregation.
    """
    bars = await _cached_bars_async(ticker, is_index, entry_date)
    if not bars:
        return "UNKNOWN"

    filt = [b for b in bars if b.timestamp.date() <= entry_date]
    if len(filt) < 30:
        return "UNKNOWN"

    closes = np.array([b.close for b in filt])
    highs = np.array([b.high for b in filt])
    lows = np.array([b.low for b in filt])
    log_rets = np.diff(np.log(closes))

    hurst = compute_hurst_exponent(log_rets)
    vol_regime_enum, _, _ = classify_volatility(log_rets)
    trend_regime_enum, _ = classify_trend(highs, lows, closes, hurst)
    ret_20d = float(closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0
    return regime_key(
        trend_regime_enum.value,
        vol_regime_enum.value,
        ret_20d,
    )


def regime_at_entry(ticker: str, entry_date: dt.date, is_index: bool) -> str:
    """Synchronous shim for offline / non-async callers.

    Uses only local-cached bars — no network fallback. Returns
    "UNKNOWN" when the local cache is insufficient. Prefer
    `regime_at_entry_async` inside the calibration sweep.
    """
    key = f"{'idx' if is_index else 'eq'}:{ticker}"
    if key in _bars_cache:
        bars = _bars_cache[key]
    else:
        bars = _load_index_bars(ticker) if is_index else _load_local_bars(ticker)
        if bars:
            _bars_cache[key] = bars
    if not bars:
        return "UNKNOWN"

    filt = [b for b in bars if b.timestamp.date() <= entry_date]
    if len(filt) < 30:
        return "UNKNOWN"

    closes = np.array([b.close for b in filt])
    highs = np.array([b.high for b in filt])
    lows = np.array([b.low for b in filt])
    log_rets = np.diff(np.log(closes))

    hurst = compute_hurst_exponent(log_rets)
    vol_regime_enum, _, _ = classify_volatility(log_rets)
    trend_regime_enum, _ = classify_trend(highs, lows, closes, hurst)
    ret_20d = float(closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0
    return regime_key(
        trend_regime_enum.value,
        vol_regime_enum.value,
        ret_20d,
    )


# ═══════════════════════════════════════════════════════════════════
#  Sweep data model
# ═══════════════════════════════════════════════════════════════════


@dataclass
class TradeRecord:
    """Single trade tagged with the regime and params that produced it."""

    strategy: str
    regime: str
    pnl_pct: float
    config: tuple  # hashable param tuple
    scenario: str = ""
    ticker: str = ""
    entry_date: str = ""

    # Used to compute trades_per_100bars — one scenario spans a fixed
    # number of trading days; we stamp that here for aggregation.
    bars_in_scenario: int = 0


def _params_to_tuple(params: dict[str, float]) -> tuple:
    """Freeze a params dict into a hashable, sortable tuple key."""
    return tuple(sorted(params.items()))


def _enumerate_configs(grid: dict[str, list[float]]) -> list[dict[str, float]]:
    """Cartesian product over the grid → list of concrete configs."""
    keys = list(grid.keys())
    combos = itertools.product(*(grid[k] for k in keys))
    return [{k: v for k, v in zip(keys, combo)} for combo in combos]


# ═══════════════════════════════════════════════════════════════════
#  Sweep driver
# ═══════════════════════════════════════════════════════════════════


async def _run_one_config(
    scenario_name: str,
    scenario_spec: dict,
    params: dict[str, float],
    semaphore: asyncio.Semaphore,
    initial_equity: float = 1_000_000.0,
) -> list[TradeRecord]:
    """Run a single (scenario × config) backtest and tag each trade."""
    async with semaphore:
        try:
            result: BacktestResult = await run_scenario_backtest(
                scenario_name,
                initial_equity=initial_equity,
                strategy_parameters=params,
            )
        except Exception as exc:
            log.warning(f"[{scenario_name}] config {params} failed: {exc}")
            return []

        # Approximate bar count from the scenario spec window.
        try:
            d0 = dt.date.fromisoformat(scenario_spec["start_date"])
            d1 = dt.date.fromisoformat(scenario_spec["end_date"])
            bar_count = max((d1 - d0).days, 1)
        except Exception:
            bar_count = 60

        records: list[TradeRecord] = []
        config_key = _params_to_tuple(params)

        for trade in result.trades:
            if not trade.is_closed:
                continue
            regime = await regime_at_entry_async(
                trade.signal.ticker,
                trade.entry_date.date(),
                is_index=False,
            )
            if regime == "UNKNOWN":
                continue
            records.append(
                TradeRecord(
                    strategy=_infer_strategy_name(trade),
                    regime=regime,
                    pnl_pct=float(trade.pnl_pct),
                    config=config_key,
                    scenario=scenario_name,
                    ticker=trade.signal.ticker,
                    entry_date=str(trade.entry_date.date()),
                    bars_in_scenario=bar_count,
                )
            )

        for fno in result.fno_trade_log:
            strategy = getattr(fno, "strategy", "")
            if not strategy:
                continue
            regime = await regime_at_entry_async(
                fno.underlying,
                fno.entry_date,
                is_index=True,
            )
            if regime == "UNKNOWN":
                continue
            records.append(
                TradeRecord(
                    strategy=strategy,
                    regime=regime,
                    pnl_pct=float(fno.pnl_pct),
                    config=config_key,
                    scenario=scenario_name,
                    ticker=fno.underlying,
                    entry_date=str(fno.entry_date),
                    bars_in_scenario=bar_count,
                )
            )

        return records


import re as _re

_STRATEGY_BRACKET_RE = _re.compile(r"^\s*\[([a-z0-9_]+)\]", _re.IGNORECASE)


def _infer_strategy_name(trade: BacktestTrade) -> str:
    """Extract the canonical strategy name from a BacktestTrade.

    The engine stamps every signal with a `[canonical_name] ...` prefix
    on the rationale. We read that first; if absent (defensive path
    for legacy trades), we fall back to keyword matching.
    """
    raw = trade.signal.rationale or ""
    m = _STRATEGY_BRACKET_RE.match(raw)
    if m:
        return m.group(1).lower()

    rationale = raw.lower()
    for prefix, canonical in [
        ("deep value", "deep_value_investment"),
        ("supply chain benef", "supply_chain_beneficiary"),
        ("supply chain capit", "supply_chain_oversold_dip"),
        ("supply chain overso", "supply_chain_oversold_dip"),
        ("trend invest", "trend_investment"),
        ("mr bounce", "mr_equity_bounce"),
    ]:
        if prefix in rationale:
            return canonical
    first = rationale.split(":", 1)[0].replace(" ", "_") or "unknown"
    return first[:50]


async def run_sweep(
    grid: dict[str, list[float]],
    scenarios: list[str],
    concurrency: int = 4,
    initial_equity: float = 1_000_000.0,
) -> list[TradeRecord]:
    """Execute the full grid × scenarios sweep asynchronously."""
    configs = _enumerate_configs(grid)
    total_tasks = len(configs) * len(scenarios)
    log.info(
        f"Starting sweep: {len(configs)} configs × {len(scenarios)} scenarios "
        f"= {total_tasks} backtests @ concurrency={concurrency}"
    )

    sem = asyncio.Semaphore(concurrency)
    t0 = time.perf_counter()

    tasks: list[asyncio.Task] = []
    for scen in scenarios:
        spec = SHOCK_SCENARIOS.get(scen)
        if not spec:
            log.warning(f"Unknown scenario '{scen}' — skipping")
            continue
        for params in configs:
            tasks.append(
                asyncio.create_task(
                    _run_one_config(scen, spec, params, sem, initial_equity),
                )
            )

    all_records: list[TradeRecord] = []
    completed = 0
    for coro in asyncio.as_completed(tasks):
        records = await coro
        all_records.extend(records)
        completed += 1
        if completed % 10 == 0 or completed == len(tasks):
            elapsed = time.perf_counter() - t0
            eta = elapsed / completed * (len(tasks) - completed)
            log.info(
                f"Sweep progress: {completed}/{len(tasks)} "
                f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining) — "
                f"{len(all_records)} trades tagged"
            )

    log.info(
        f"Sweep complete: {len(all_records)} trades across "
        f"{len(tasks)} backtests in {time.perf_counter() - t0:.0f}s"
    )
    return all_records


# ═══════════════════════════════════════════════════════════════════
#  Aggregation → priors
# ═══════════════════════════════════════════════════════════════════


def _per_trade_sharpe(pnl_pcts: list[float]) -> float:
    """Per-trade Sharpe = mean(R) / std(R). R is realised return per trade.

    We deliberately do NOT annualise — the calibration goal is to pick
    the best *trade selection* policy, not the capital deployment
    rate. When the Supervisor reads the prior, it is comparing the
    across-regime RELATIVE ordering of configs, not absolute Sharpe.
    """
    if len(pnl_pcts) < 2:
        return 0.0
    arr = np.asarray(pnl_pcts, dtype=float)
    mu = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    if sd < 1e-10:
        return 0.0
    return mu / sd


def _sharpe_ci95(pnl_pcts: list[float]) -> tuple[float, float]:
    """Normal-approximation 95% CI for the per-trade Sharpe.

    Using Lo (2002) standard-error formula:
        SE(Sharpe) ≈ sqrt((1 + 0.5 × Sharpe²) / n)

    Bootstrap CIs would be more robust on small n but would add a
    slow inner loop; this is the institutional quick-and-defensible
    default for n ≥ 5.
    """
    n = len(pnl_pcts)
    if n < 2:
        return (0.0, 0.0)
    sharpe = _per_trade_sharpe(pnl_pcts)
    se = math.sqrt((1.0 + 0.5 * sharpe * sharpe) / n)
    return (round(sharpe - 1.96 * se, 3), round(sharpe + 1.96 * se, 3))


def _extract_params_from_config(config: tuple) -> dict[str, float]:
    """Unfreeze the hashable config tuple back to a dict."""
    return {k: v for k, v in config}


def aggregate_priors(
    records: list[TradeRecord],
    min_samples: int = MIN_CELL_SAMPLES,
) -> dict[str, Any]:
    """Group trades by (strategy, regime) → best config per cell.

    Produces the priors.json payload. The "best" config maximises per-
    trade Sharpe with a hard sample-size floor. Cells that never saw a
    positive-Sharpe config are marked `blocked_by_regime: true`.
    """
    # Step 1 — bucket into {(strategy, regime, config): [pnl_pcts]}
    buckets: dict[tuple[str, str, tuple], list[float]] = {}
    bars_per_scenario: dict[str, int] = {}
    for r in records:
        key = (r.strategy, r.regime, r.config)
        buckets.setdefault(key, []).append(r.pnl_pct)
        bars_per_scenario[r.scenario] = r.bars_in_scenario

    # Step 2 — for each (strategy, regime) pick the best config.
    priors: dict[str, dict[str, Any]] = {}
    best_per_cell: dict[tuple[str, str], tuple[tuple, float, int, tuple]] = {}
    for (strategy, regime, config), pnls in buckets.items():
        if len(pnls) < min_samples:
            continue
        sharpe = _per_trade_sharpe(pnls)
        ci = _sharpe_ci95(pnls)
        current = best_per_cell.get((strategy, regime))
        if current is None or sharpe > current[1]:
            best_per_cell[(strategy, regime)] = (config, sharpe, len(pnls), ci)

    # Step 3 — emit the structured priors payload.
    for (strategy, regime), (config, sharpe, n, ci) in best_per_cell.items():
        params = _extract_params_from_config(config)
        wins = len([p for p in buckets[(strategy, regime, config)] if p > 0])
        total = len(buckets[(strategy, regime, config)])
        win_rate = wins / total if total > 0 else 0.0
        total_bars = sum(bars_per_scenario.values()) or 1
        trades_per_100 = round(total / total_bars * 100.0, 2)

        priors.setdefault(strategy, {})[regime] = {
            "max_rsi": params.get("max_rsi_entry_long"),
            "max_z": params.get("max_z_entry_long"),
            "min_factor_score": params.get("min_factor_score"),
            "min_expected_r": params.get("min_expected_r"),
            "sharpe": round(sharpe, 3),
            "ci": list(ci),
            "n": n,
            "win_rate": round(win_rate, 3),
            "trades_per_100bars": trades_per_100,
        }

    # Step 4 — mark (strategy, regime) cells that never produced a
    # positive-Sharpe config with min_samples. Downstream this is how
    # the orchestrator knows to simply skip that combo.
    all_strategies = {r.strategy for r in records}
    all_regimes = {r.regime for r in records}
    for strategy in all_strategies:
        for regime in all_regimes:
            existing = priors.get(strategy, {}).get(regime)
            if existing and existing.get("sharpe", 0.0) > 0:
                continue
            # Check if ANY config for this cell was positive; if none
            # were positive the regime blocks this strategy.
            cell_configs = [
                (pnls, _per_trade_sharpe(pnls))
                for (s, r_, _), pnls in buckets.items()
                if s == strategy and r_ == regime and len(pnls) >= min_samples
            ]
            if cell_configs and max(sh for _, sh in cell_configs) > 0.0:
                continue  # a positive-sharpe config exists but wasn't top pick
            priors.setdefault(strategy, {})[regime] = {
                "blocked_by_regime": True,
                "n": max((len(p) for p, _ in cell_configs), default=0),
                "best_sharpe_seen": round(max((sh for _, sh in cell_configs), default=0.0), 3),
            }

    # ── Win rate floor auto-block ──
    # Block strategy×regime cells where win_rate < 0.40 AND n >= 15.
    # Prevents low-WR cells from dragging down blended metrics.
    for strategy in all_strategies:
        for regime in all_regimes:
            cell = priors.get(strategy, {}).get(regime, {})
            if cell.get("blocked_by_regime"):
                continue
            cell_pnls = []
            for (s, r_, _), pnls in buckets.items():
                if s == strategy and r_ == regime:
                    cell_pnls.extend(pnls)
            if len(cell_pnls) >= 15:
                wins = sum(1 for p in cell_pnls if p > 0)
                wr = wins / len(cell_pnls)
                if wr < 0.40:
                    priors.setdefault(strategy, {})[regime] = {
                        "blocked_by_regime": True,
                        "n": len(cell_pnls),
                        "_block_reason": f"auto-blocked: win_rate={wr:.2f} < 0.40 floor (n={len(cell_pnls)})",
                    }

    return priors


def write_priors(priors: dict[str, Any], path: Path) -> None:
    """Persist priors to disk with a timestamp + metadata header."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "_min_samples": MIN_CELL_SAMPLES,
        "_schema_version": 1,
        "priors": priors,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    log.info(f"Wrote priors for {len(priors)} strategies → {path}")


# ═══════════════════════════════════════════════════════════════════
#  Public entrypoint
# ═══════════════════════════════════════════════════════════════════

DEFAULT_PRIORS_PATH = Path("calibration/priors.json")


async def calibrate(
    mode: str = "quick",
    scenarios: list[str] | None = None,
    concurrency: int = 4,
    output: Path = DEFAULT_PRIORS_PATH,
    initial_equity: float = 1_000_000.0,
) -> dict[str, Any]:
    """One-shot calibration run — sweep → aggregate → write priors."""
    grid = GRID_PRESETS.get(mode, QUICK_GRID)
    if scenarios is None:
        if mode == "quick":
            scenarios = [
                "covid_crash_2020",
                "svb_collapse_2023",
                "chatgpt_ai_rally_2023",
            ]
        elif mode == "medium":
            scenarios = list(SHOCK_SCENARIOS.keys())[:8]
        else:
            scenarios = list(SHOCK_SCENARIOS.keys())

    records = await run_sweep(
        grid=grid,
        scenarios=scenarios,
        concurrency=concurrency,
        initial_equity=initial_equity,
    )
    priors = aggregate_priors(records)
    write_priors(priors, output)
    return priors


def _cli() -> None:
    parser = argparse.ArgumentParser(description="AYVID calibration harness")
    parser.add_argument("--mode", choices=list(GRID_PRESETS.keys()), default="quick")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--scenarios",
        type=str,
        default=None,
        help="Comma-separated scenario keys; defaults depend on --mode.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_PRIORS_PATH)
    parser.add_argument("--initial-equity", type=float, default=1_000_000.0)
    args = parser.parse_args()

    scenarios = (
        [s.strip() for s in args.scenarios.split(",") if s.strip()] if args.scenarios else None
    )

    asyncio.run(
        calibrate(
            mode=args.mode,
            scenarios=scenarios,
            concurrency=args.concurrency,
            output=args.output,
            initial_equity=args.initial_equity,
        )
    )


if __name__ == "__main__":
    _cli()
