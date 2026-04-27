"""Realistic transaction cost model for the backtest engine.

Calibrated to Interactive Brokers Tiered pricing schedule (institutional rates)
and observed bid/ask slippage on the underlyings AYVID actually trades.

Used at every entry and exit in `backtesting.engine` so reported PnL,
Sharpe, win rate, and R-multiples reflect post-cost performance.

References (public, as of 2026):
  - IBKR Pro Tiered: US options $0.65/contract first 100, $0.50 next ...
  - IBKR Pro Tiered: US futures ES/NQ ~$0.85/side, micros $0.25/side
  - Equities: ~$0.0035/share, $0.35 min, capped at 1% of trade value
  - Index F&O outside US (NIFTY/HSI/DAX/etc.): ~$1.20/contract effective
  - Bid/ask slippage: 1 tick per side for liquid index F&O,
    half-cent for liquid US equities; we add a realistic conservative
    haircut to mimic real fills, not theoretical Black-Scholes mids.
"""
from __future__ import annotations

from dataclasses import dataclass


# ── Commission rates (per contract / per share, USD) ──────────
COMMISSION_PER_OPTION_CONTRACT = 0.65
COMMISSION_PER_FUTURE_CONTRACT = 0.85
COMMISSION_PER_NON_US_FUTURE_CONTRACT = 1.20
COMMISSION_PER_SHARE = 0.0035
COMMISSION_MIN_EQUITY = 0.35
COMMISSION_MAX_EQUITY_PCT = 0.01

# ── Slippage assumptions (in price-points, per side) ──────────
# Index options: half a tick on the bid/ask is typical for liquid
# weeklies on ES/NIFTY/DAX. We charge a full tick to be conservative.
SLIPPAGE_OPTION_PCT_OF_PREMIUM = 0.015
SLIPPAGE_FUTURE_PCT_OF_PRICE = 0.0002
SLIPPAGE_EQUITY_PCT_OF_PRICE = 0.0005

# Indices known to be illiquid for retail-routed flow → wider haircut
ILLIQUID_INDEX_SYMBOLS: set[str] = {
    "^HSI",          # Hang Seng — wider spreads, fewer market-makers
    "000001.SS",     # CSI 300 — restricted access
    "^TWII",         # TAIEX — 5pt tick, illiquid weeklies
    "^STI",          # STI — limited option depth
    "^AXJO",         # ASX 200 — wide quoting
    "^BVSP",         # Bovespa — illiquid options
}
ILLIQUID_SLIPPAGE_MULTIPLIER = 2.0


@dataclass(frozen=True)
class TradeCosts:
    """Round-trip cost breakdown for a single trade."""

    commission: float
    slippage_entry: float
    slippage_exit: float

    @property
    def total(self) -> float:
        return self.commission + self.slippage_entry + self.slippage_exit


def _is_us_index(symbol: str) -> bool:
    return symbol in {"^GSPC", "^IXIC", "^DJI", "^RUT"}


def option_costs(
    underlying: str,
    entry_premium: float,
    exit_premium: float,
    lots: int,
    multiplier: float,
    usd_rate: float,
) -> TradeCosts:
    """Round-trip cost for an index option (single leg)."""
    commission = 2 * COMMISSION_PER_OPTION_CONTRACT * lots
    slip_pct = SLIPPAGE_OPTION_PCT_OF_PREMIUM
    if underlying in ILLIQUID_INDEX_SYMBOLS:
        slip_pct *= ILLIQUID_SLIPPAGE_MULTIPLIER

    slip_entry_per_lot = entry_premium * slip_pct * multiplier * usd_rate
    slip_exit_per_lot = exit_premium * slip_pct * multiplier * usd_rate
    return TradeCosts(
        commission=commission,
        slippage_entry=slip_entry_per_lot * lots,
        slippage_exit=slip_exit_per_lot * lots,
    )


def spread_costs(
    underlying: str,
    net_debit: float,
    exit_value: float,
    lots: int,
    multiplier: float,
    usd_rate: float,
) -> TradeCosts:
    """Round-trip cost for a vertical spread (two legs each side)."""
    commission = 4 * COMMISSION_PER_OPTION_CONTRACT * lots
    slip_pct = SLIPPAGE_OPTION_PCT_OF_PREMIUM * 1.5
    if underlying in ILLIQUID_INDEX_SYMBOLS:
        slip_pct *= ILLIQUID_SLIPPAGE_MULTIPLIER

    slip_entry_per_lot = net_debit * slip_pct * multiplier * usd_rate
    slip_exit_per_lot = max(exit_value, 0.01) * slip_pct * multiplier * usd_rate
    return TradeCosts(
        commission=commission,
        slippage_entry=slip_entry_per_lot * lots,
        slippage_exit=slip_exit_per_lot * lots,
    )


def future_costs(
    underlying: str,
    entry_price: float,
    exit_price: float,
    lots: int,
    multiplier: float,
    usd_rate: float,
) -> TradeCosts:
    """Round-trip cost for an index future."""
    per_contract = (
        COMMISSION_PER_FUTURE_CONTRACT
        if _is_us_index(underlying)
        else COMMISSION_PER_NON_US_FUTURE_CONTRACT
    )
    commission = 2 * per_contract * lots
    slip_pct = SLIPPAGE_FUTURE_PCT_OF_PRICE
    if underlying in ILLIQUID_INDEX_SYMBOLS:
        slip_pct *= ILLIQUID_SLIPPAGE_MULTIPLIER

    slip_entry_per_lot = entry_price * slip_pct * multiplier * usd_rate
    slip_exit_per_lot = exit_price * slip_pct * multiplier * usd_rate
    return TradeCosts(
        commission=commission,
        slippage_entry=slip_entry_per_lot * lots,
        slippage_exit=slip_exit_per_lot * lots,
    )


def equity_costs(
    entry_price: float,
    exit_price: float,
    shares: int,
) -> TradeCosts:
    """Round-trip cost for a single equity position.

    Applies IBKR Tiered: $0.0035/share, $0.35 min per side,
    capped at 1% of notional per side.
    """
    def _side_commission(price: float) -> float:
        raw = COMMISSION_PER_SHARE * shares
        cap = COMMISSION_MAX_EQUITY_PCT * price * shares
        return min(max(raw, COMMISSION_MIN_EQUITY), cap)

    commission = _side_commission(entry_price) + _side_commission(exit_price)
    slip_entry = entry_price * SLIPPAGE_EQUITY_PCT_OF_PRICE * shares
    slip_exit = exit_price * SLIPPAGE_EQUITY_PCT_OF_PRICE * shares
    return TradeCosts(
        commission=commission,
        slippage_entry=slip_entry,
        slippage_exit=slip_exit,
    )


COST_DISCLOSURE = """\
Transaction cost model (IBKR Tiered, conservative):
  - Index options: $0.65/contract per side + 1.5% premium slippage per side
  - Vertical spreads: $0.65/contract per leg per side + 2.25% slippage
  - Index futures (US): $0.85/contract per side + 2bps slippage
  - Index futures (non-US): $1.20/contract per side + 2bps slippage
  - Equities: $0.0035/share, $0.35 min, capped at 1% notional + 5bps slippage
  - Illiquid indices (HSI, CSI 300, TAIEX, STI, ASX, Bovespa): 2x slippage
All PnL, Sharpe, win rate, and R-multiple metrics in this report are
post-cost.
"""
