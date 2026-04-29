"""Unusual Options Activity (UOA) scanner — detects institutional smart money.

Block trades at 10x average daily volume on short-dated options indicate
directional institutional bets. When detected, this signal boosts the
alpha factor score significantly.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.logger import get_logger

log = get_logger("data.options_flow")


@dataclass
class UOASignal:
    """Unusual Options Activity detection result."""

    is_unusual: bool = False
    direction: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
    confidence: float = 0.0
    expiry_urgency: float = 0.0  # higher = shorter DTE = more urgent


async def scan_unusual_activity(ticker: str) -> UOASignal:
    """Detect unusual options activity for a given ticker.

    Returns UOASignal with direction and confidence. When volume on
    short-dated calls/puts exceeds 10x average daily OI, the signal
    fires with directional bias.

    Falls back gracefully when options data is unavailable.
    """
    try:
        from data_primitives.extractors.openbb_client import get_openbb_client

        client = get_openbb_client()
        chains = await client.fetch_options_chains(ticker)
        if chains is None or chains.empty:
            return UOASignal()

        avg_daily_vol = chains["volume"].mean() if "volume" in chains.columns else 0
        if avg_daily_vol < 1:
            return UOASignal()

        # Filter for short-dated, high-volume contracts
        short_dated = chains[chains["dte"] < 30] if "dte" in chains.columns else chains

        unusual_calls = short_dated[
            (short_dated.get("option_type", short_dated.get("right", "")) == "call")
            & (short_dated["volume"] > avg_daily_vol * 10)
        ] if not short_dated.empty else short_dated.iloc[:0]

        unusual_puts = short_dated[
            (short_dated.get("option_type", short_dated.get("right", "")) == "put")
            & (short_dated["volume"] > avg_daily_vol * 10)
        ] if not short_dated.empty else short_dated.iloc[:0]

        call_premium = float(
            (unusual_calls["volume"] * unusual_calls["ask"]).sum()
        ) if not unusual_calls.empty and "ask" in unusual_calls.columns else 0.0

        put_premium = float(
            (unusual_puts["volume"] * unusual_puts["ask"]).sum()
        ) if not unusual_puts.empty and "ask" in unusual_puts.columns else 0.0

        total = call_premium + put_premium + 1.0  # avoid div by zero

        if call_premium > put_premium * 2:
            direction = "BULLISH"
            confidence = min(call_premium / total, 0.95)
        elif put_premium > call_premium * 2:
            direction = "BEARISH"
            confidence = min(put_premium / total, 0.95)
        else:
            direction = "NEUTRAL"
            confidence = 0.0

        # Expiry urgency: shorter DTE = more urgent signal
        min_dte = float(short_dated["dte"].min()) if "dte" in short_dated.columns and not short_dated.empty else 30.0
        expiry_urgency = max(0.0, 1.0 - min_dte / 30.0)

        return UOASignal(
            is_unusual=confidence > 0.5,
            direction=direction,
            confidence=round(confidence, 3),
            expiry_urgency=round(expiry_urgency, 3),
        )

    except Exception as exc:
        log.debug(f"UOA scan failed for {ticker}: {exc}")
        return UOASignal()
