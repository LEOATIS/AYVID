"""Global Market Scanner — real-time multi-asset shock detection.

Scans across all asset classes and world markets to detect:
- Abnormal price moves (> 1.2 sigma default, supply-chain 1.0 sigma)
- Volume spikes (> 1.8x average, supply-chain 1.5x)
- Opening gaps (> 1.5%, supply-chain 1.0%)
- Correlation breakdowns
- Cross-asset contagion signals
- Sector rotation shifts

This feeds directly into the LangGraph causal analysis pipeline.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import numpy as np

from shared.logger import get_logger
from shared.types import AssetClass, CausalEvent, EventType, OHLCVBar

log = get_logger("data.scanner")


class ScanResult:
    """A detected market anomaly from the global scan."""

    def __init__(
        self,
        symbol: str,
        asset_class: AssetClass,
        event_type: EventType,
        severity: float,
        description: str,
        price_change_pct: float = 0.0,
        volume_ratio: float = 0.0,
    ):
        self.symbol = symbol
        self.asset_class = asset_class
        self.event_type = event_type
        self.severity = severity
        self.description = description
        self.price_change_pct = price_change_pct
        self.volume_ratio = volume_ratio
        self.timestamp = dt.datetime.now(dt.timezone.utc)

    def to_causal_event(self) -> CausalEvent:
        return CausalEvent(
            timestamp=self.timestamp,
            source_node=self.symbol,
            event_type=self.event_type,
            compressed_summary=self.description,
            confidence=min(abs(self.severity) / 5.0, 1.0),
        )

    def __repr__(self) -> str:
        return f"ScanResult({self.symbol} | {self.event_type.value} | severity={self.severity:.2f})"


def detect_price_anomaly(
    bars: list[OHLCVBar],
    sigma_threshold: float = 1.2,
    lookback: int = 20,
) -> ScanResult | None:
    """Detect abnormal price movement (> N sigma from rolling mean)."""
    if len(bars) < lookback + 1:
        return None

    closes = np.array([b.close for b in bars])
    returns = np.diff(np.log(closes))

    if len(returns) < lookback:
        return None

    rolling_mean = np.mean(returns[-lookback:])
    rolling_std = np.std(returns[-lookback:], ddof=1)

    if rolling_std == 0:
        return None

    latest_return = returns[-1]
    z_score = (latest_return - rolling_mean) / rolling_std
    price_change_pct = (closes[-1] / closes[-2] - 1) * 100

    if abs(z_score) >= sigma_threshold:
        direction = "crashed" if z_score < 0 else "surged"
        event_type = EventType.SHOCK if z_score < -sigma_threshold else EventType.RECOVERY
        return ScanResult(
            symbol=bars[0].symbol,
            asset_class=AssetClass.EQUITY,
            event_type=event_type,
            severity=float(abs(z_score)),
            description=f"{bars[0].symbol} {direction} {price_change_pct:+.2f}% ({abs(z_score):.1f} sigma)",
            price_change_pct=price_change_pct,
        )
    return None


def detect_volume_spike(
    bars: list[OHLCVBar],
    volume_ratio_threshold: float = 1.8,
    lookback: int = 20,
) -> ScanResult | None:
    """Detect abnormal volume (> Nx average daily volume)."""
    if len(bars) < lookback + 1:
        return None

    volumes = np.array([b.volume for b in bars])
    avg_volume = np.mean(volumes[-lookback - 1:-1])

    if avg_volume == 0:
        return None

    latest_volume = volumes[-1]
    ratio = latest_volume / avg_volume

    if ratio >= volume_ratio_threshold:
        price_change = (bars[-1].close / bars[-2].close - 1) * 100 if len(bars) > 1 else 0
        return ScanResult(
            symbol=bars[0].symbol,
            asset_class=AssetClass.EQUITY,
            event_type=EventType.SHOCK,
            severity=float(ratio),
            description=f"{bars[0].symbol} volume spike {ratio:.1f}x avg (price {price_change:+.1f}%)",
            volume_ratio=float(ratio),
            price_change_pct=price_change,
        )
    return None


def detect_gap(
    bars: list[OHLCVBar],
    gap_threshold_pct: float = 1.5,
) -> ScanResult | None:
    """Detect opening gaps (gap up/down > threshold)."""
    if len(bars) < 2:
        return None

    prev_close = bars[-2].close
    curr_open = bars[-1].open
    gap_pct = (curr_open / prev_close - 1) * 100

    if abs(gap_pct) >= gap_threshold_pct:
        direction = "gap up" if gap_pct > 0 else "gap down"
        return ScanResult(
            symbol=bars[0].symbol,
            asset_class=AssetClass.EQUITY,
            event_type=EventType.SHOCK,
            severity=abs(gap_pct) / gap_threshold_pct,
            description=f"{bars[0].symbol} {direction} {gap_pct:+.2f}% at open",
            price_change_pct=gap_pct,
        )
    return None


async def scan_universe(
    price_data: dict[str, list[OHLCVBar]],
    sigma_threshold: float = 1.2,
    volume_threshold: float = 1.8,
    gap_threshold: float = 1.5,
) -> list[ScanResult]:
    """Run all anomaly detectors across the entire universe.

    This is the main entry point for the global scanner.
    """
    results: list[ScanResult] = []

    for symbol, bars in price_data.items():
        if not bars or len(bars) < 5:
            continue

        price_anomaly = detect_price_anomaly(bars, sigma_threshold)
        if price_anomaly:
            results.append(price_anomaly)

        vol_spike = detect_volume_spike(bars, volume_threshold)
        if vol_spike:
            results.append(vol_spike)

        gap = detect_gap(bars, gap_threshold)
        if gap:
            results.append(gap)

    results.sort(key=lambda r: r.severity, reverse=True)

    if results:
        log.info(
            f"Scanner found [bold]{len(results)}[/bold] anomalies "
            f"across {len(price_data)} instruments"
        )
    return results


async def scan_for_supply_chain_shocks(
    price_data: dict[str, list[OHLCVBar]],
    supply_chain_tickers: list[str] | None = None,
) -> list[ScanResult]:
    """Focused scan on supply-chain-critical instruments.

    Prioritizes semiconductor, shipping, commodity, and infrastructure tickers.
    """
    from data_primitives.extractors.universe_builder import get_supply_chain_critical_tickers

    critical = supply_chain_tickers or get_supply_chain_critical_tickers()
    filtered = {k: v for k, v in price_data.items() if k in critical}

    return await scan_universe(
        filtered,
        sigma_threshold=1.0,
        volume_threshold=1.5,
        gap_threshold=1.0,
    )


async def cross_asset_correlation_scan(
    equity_data: dict[str, list[OHLCVBar]],
    commodity_data: dict[str, list[OHLCVBar]],
    fx_data: dict[str, list[OHLCVBar]],
) -> list[ScanResult]:
    """Detect cross-asset contagion — when normally uncorrelated assets move together.

    A simultaneous drop in semis + oil spike + USD/CNY move = supply chain shock signal.
    """
    results: list[ScanResult] = []

    for category_name, data in [
        ("EQUITY", equity_data),
        ("COMMODITY", commodity_data),
        ("FX", fx_data),
    ]:
        shocks = await scan_universe(data, sigma_threshold=2.0)
        for shock in shocks:
            shock.asset_class = AssetClass[category_name] if category_name in AssetClass.__members__ else AssetClass.EQUITY
            results.append(shock)

    if len(results) > 3:
        affected_classes = set(r.asset_class for r in results[:10])
        if len(affected_classes) >= 2:
            results.insert(0, ScanResult(
                symbol="CROSS_ASSET",
                asset_class=AssetClass.INDEX,
                event_type=EventType.SHOCK,
                severity=max(r.severity for r in results),
                description=f"Cross-asset contagion: {len(results)} anomalies across {len(affected_classes)} asset classes",
            ))

    return results
