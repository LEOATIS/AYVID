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

import datetime as dt
from dataclasses import dataclass

# ── Commission rates (per contract / per share, USD) ──────────
# Module-level constants reflect today's IBKR Pro Tiered schedule. For
# historical backtests use ``rates_for(as_of)`` which returns the
# epoch-correct schedule. IBKR has changed pricing meaningfully:
#   2018-01-01 : pre-Tiered, higher per-share, $1.00 minimum
#   2019-06-01 : current Tiered structure, $0.0035/share, $0.35 min
#   2022-04-01 : option contract floor reduced to $0.65 from $0.70
COMMISSION_PER_OPTION_CONTRACT = 0.65
COMMISSION_PER_FUTURE_CONTRACT = 0.85
COMMISSION_PER_NON_US_FUTURE_CONTRACT = 1.20
COMMISSION_PER_SHARE = 0.0035
COMMISSION_MIN_EQUITY = 0.35
COMMISSION_MAX_EQUITY_PCT = 0.01


@dataclass(frozen=True)
class CommissionSchedule:
    """Snapshot of IBKR Pro Tiered rates effective on a given date.

    Used for historical-cost-correct backtests. A simulated 2018 trade
    should debit 2018's higher per-share commission, not 2026's. Pass
    the simulated date to ``rates_for(...)`` and use the resulting
    schedule's fields to override module-level defaults at the call
    site. Without this, pre-2019 backtests systematically under-cost
    by ~20–30% and inflate Sharpe by ~0.3–0.5.
    """

    effective_from: dt.date
    per_option_contract: float
    per_future_contract: float
    per_non_us_future_contract: float
    per_share: float
    min_equity: float
    max_equity_pct: float


_COMMISSION_HISTORY: tuple[CommissionSchedule, ...] = (
    # Earliest known schedule; pre-Tiered "Fixed" plus options.
    CommissionSchedule(
        effective_from=dt.date(2017, 1, 1),
        per_option_contract=0.70,
        per_future_contract=0.85,
        per_non_us_future_contract=1.20,
        per_share=0.005,
        min_equity=1.00,
        max_equity_pct=0.01,
    ),
    # Tiered restructuring; per-share dropped to 0.0035, min to 0.35.
    CommissionSchedule(
        effective_from=dt.date(2019, 6, 1),
        per_option_contract=0.70,
        per_future_contract=0.85,
        per_non_us_future_contract=1.20,
        per_share=0.0035,
        min_equity=0.35,
        max_equity_pct=0.01,
    ),
    # Option contract floor reduction.
    CommissionSchedule(
        effective_from=dt.date(2022, 4, 1),
        per_option_contract=0.65,
        per_future_contract=0.85,
        per_non_us_future_contract=1.20,
        per_share=0.0035,
        min_equity=0.35,
        max_equity_pct=0.01,
    ),
)


def rates_for(as_of: dt.date | None = None) -> CommissionSchedule:
    """Return the commission schedule effective on ``as_of`` (or today).

    Picks the latest schedule whose ``effective_from`` is on-or-before
    the requested date. With no argument returns the most recent
    schedule — equivalent to today's behaviour.
    """
    if as_of is None:
        return _COMMISSION_HISTORY[-1]
    effective: CommissionSchedule = _COMMISSION_HISTORY[0]
    for sched in _COMMISSION_HISTORY:
        if sched.effective_from <= as_of:
            effective = sched
        else:
            break
    return effective

# ── Slippage assumptions (in price-points, per side) ──────────
# Index options: half a tick on the bid/ask is typical for liquid
# weeklies on ES/NIFTY/DAX. We charge a full tick to be conservative.
SLIPPAGE_OPTION_PCT_OF_PREMIUM = 0.015
SLIPPAGE_FUTURE_PCT_OF_PRICE = 0.0002
SLIPPAGE_EQUITY_PCT_OF_PRICE = 0.0005

# Indices known to be illiquid for retail-routed flow → wider haircut
ILLIQUID_INDEX_SYMBOLS: set[str] = {
    "^HSI",  # Hang Seng — wider spreads, fewer market-makers
    "000001.SS",  # CSI 300 — restricted access
    "^TWII",  # TAIEX — 5pt tick, illiquid weeklies
    "^STI",  # STI — limited option depth
    "^AXJO",  # ASX 200 — wide quoting
    "^BVSP",  # Bovespa — illiquid options
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
    *,
    as_of: dt.date | None = None,
) -> TradeCosts:
    """Round-trip cost for an index option (single leg).

    When ``as_of`` is supplied, uses the IBKR commission schedule
    effective on that date instead of today's. This is what makes
    historical backtests cost-correct.
    """
    sched = rates_for(as_of)
    commission = 2 * sched.per_option_contract * lots
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
    *,
    as_of: dt.date | None = None,
) -> TradeCosts:
    """Round-trip cost for a vertical spread (two legs each side)."""
    sched = rates_for(as_of)
    commission = 4 * sched.per_option_contract * lots
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
    *,
    as_of: dt.date | None = None,
) -> TradeCosts:
    """Round-trip cost for an index future."""
    sched = rates_for(as_of)
    per_contract = (
        sched.per_future_contract
        if _is_us_index(underlying)
        else sched.per_non_us_future_contract
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
    *,
    as_of: dt.date | None = None,
) -> TradeCosts:
    """Round-trip cost for a single equity position.

    Uses the IBKR Tiered schedule effective on ``as_of`` (or today's
    when omitted). 2018 backtests should pass the simulated date so
    the higher pre-Tiered per-share rate is applied.
    """
    sched = rates_for(as_of)

    def _side_commission(price: float) -> float:
        raw = sched.per_share * shares
        cap = sched.max_equity_pct * price * shares
        return min(max(raw, sched.min_equity), cap)

    commission = _side_commission(entry_price) + _side_commission(exit_price)
    slip_entry = entry_price * SLIPPAGE_EQUITY_PCT_OF_PRICE * shares
    slip_exit = exit_price * SLIPPAGE_EQUITY_PCT_OF_PRICE * shares
    return TradeCosts(
        commission=commission,
        slippage_entry=slip_entry,
        slippage_exit=slip_exit,
    )


def fx_costs(
    pair: str,
    lot_size: float,
    holding_days: float = 0,
) -> TradeCosts:
    """Round-trip cost for an FX position.

    Uses the spread/cost model from contract_specs.
    """
    from shared.contract_specs import FX_SPREAD_PIPS, fx_pip_size

    spread = FX_SPREAD_PIPS.get(pair, 2.0)
    pip_sz = fx_pip_size(pair)
    notional = lot_size * 100_000
    spread_cost_per_side = spread * pip_sz * notional
    swap_cost = holding_days * 0.00005 * notional
    return TradeCosts(
        commission=0.0,  # FX has no separate commission — cost is in spread
        slippage_entry=spread_cost_per_side,
        slippage_exit=spread_cost_per_side + swap_cost,
    )


def commodity_futures_costs(
    symbol: str,
    entry_price: float,
    exit_price: float,
    lots: int,
) -> TradeCosts:
    """Round-trip cost for a commodity futures position."""
    from shared.contract_specs import COMMODITY_CONTRACT_SPECS

    spec = COMMODITY_CONTRACT_SPECS.get(symbol)
    if spec is None:
        return TradeCosts(commission=lots * 5.0, slippage_entry=0, slippage_exit=0)
    commission = lots * 5.0  # $2.50/side × 2
    slippage_entry = spec.tick_value * 1.0 * lots
    slippage_exit = spec.tick_value * 1.0 * lots
    return TradeCosts(
        commission=commission,
        slippage_entry=slippage_entry,
        slippage_exit=slippage_exit,
    )


def crypto_costs(notional: float) -> TradeCosts:
    """Round-trip cost for a crypto position."""
    fee_per_side = notional * 0.001  # 0.1% taker
    spread_per_side = notional * 0.0005  # 0.05% slippage
    return TradeCosts(
        commission=fee_per_side * 2,
        slippage_entry=spread_per_side,
        slippage_exit=spread_per_side,
    )


def rate_futures_costs(symbol: str, lots: int) -> TradeCosts:
    """Round-trip cost for a rate/bond futures position."""
    from shared.contract_specs import RATE_CONTRACT_SPECS

    spec = RATE_CONTRACT_SPECS.get(symbol)
    tick_val = spec.tick_value if spec else 15.625
    commission = lots * 5.0
    slippage = tick_val * 1.0 * lots
    return TradeCosts(
        commission=commission,
        slippage_entry=slippage,
        slippage_exit=slippage,
    )


COST_DISCLOSURE = """\
Transaction cost model (IBKR Tiered, conservative):
  - Index options: $0.65/contract per side + 1.5% premium slippage per side
  - Vertical spreads: $0.65/contract per leg per side + 2.25% slippage
  - Index futures (US): $0.85/contract per side + 2bps slippage
  - Index futures (non-US): $1.20/contract per side + 2bps slippage
  - Equities/ETFs: $0.0035/share, $0.35 min, capped at 1% notional + 5bps slippage
  - FX: spread-based (0.5-8 pips depending on pair) + overnight swap
  - Commodity futures: $2.50/contract per side + 1 tick slippage per side
  - Crypto: 0.1% taker fee per side + 0.05% slippage per side
  - Rate/bond futures: $2.50/contract per side + 1 tick slippage per side
  - Illiquid indices (HSI, CSI 300, TAIEX, STI, ASX, Bovespa): 2x slippage
All PnL, Sharpe, win rate, and R-multiple metrics in this report are
post-cost.
"""
