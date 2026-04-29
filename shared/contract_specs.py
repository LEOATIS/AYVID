"""Contract specifications for futures, FX, and crypto instruments.

Single source of truth for multipliers, tick sizes, margin requirements,
and cost parameters across all non-equity asset classes. Used by:
  - backtesting/engine.py (multi-asset simulation)
  - execution_engine/paper_runner.py (live order sizing)
  - shared/cost_model.py (transaction cost calculation)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    name: str
    exchange: str
    multiplier: float
    tick_size: float
    tick_value: float
    margin_pct: float
    currency: str = "USD"


@dataclass(frozen=True)
class FXSpec:
    pair: str
    spread_pips: float
    pip_value: float  # value of 1 pip per standard lot (100k units)
    base_currency: str = ""
    quote_currency: str = ""


# ── Commodity Futures ────────────────────────────────────────

COMMODITY_CONTRACT_SPECS: dict[str, ContractSpec] = {
    "CL": ContractSpec("Crude Oil WTI", "NYMEX", 1000, 0.01, 10.0, 0.05),
    "BZ": ContractSpec("Brent Crude", "ICE", 1000, 0.01, 10.0, 0.05),
    "NG": ContractSpec("Natural Gas", "NYMEX", 10000, 0.001, 10.0, 0.06),
    "GC": ContractSpec("Gold", "COMEX", 100, 0.10, 10.0, 0.05),
    "SI": ContractSpec("Silver", "COMEX", 5000, 0.005, 25.0, 0.07),
    "HG": ContractSpec("Copper", "COMEX", 25000, 0.0005, 12.50, 0.06),
    "PL": ContractSpec("Platinum", "NYMEX", 50, 0.10, 5.0, 0.06),
    "PA": ContractSpec("Palladium", "NYMEX", 100, 0.05, 5.0, 0.08),
    "ZC": ContractSpec("Corn", "CBOT", 5000, 0.25, 12.50, 0.04),
    "ZS": ContractSpec("Soybeans", "CBOT", 5000, 0.25, 12.50, 0.04),
    "ZW": ContractSpec("Wheat", "CBOT", 5000, 0.25, 12.50, 0.04),
    "CT": ContractSpec("Cotton", "ICE", 50000, 0.01, 5.0, 0.05),
    "KC": ContractSpec("Coffee", "ICE", 37500, 0.05, 18.75, 0.06),
    "SB": ContractSpec("Sugar", "ICE", 112000, 0.01, 11.20, 0.05),
    "CC": ContractSpec("Cocoa", "ICE", 10, 1.0, 10.0, 0.06),
    "LE": ContractSpec("Live Cattle", "CME", 40000, 0.025, 10.0, 0.04),
    "HE": ContractSpec("Lean Hogs", "CME", 40000, 0.025, 10.0, 0.04),
    "LBS": ContractSpec("Lumber", "CME", 27500, 0.10, 2.75, 0.08),
}

# ── Rate / Bond Futures ──────────────────────────────────────

RATE_CONTRACT_SPECS: dict[str, ContractSpec] = {
    "ZB": ContractSpec("US Treasury Bond 30Y", "CBOT", 1000, 1 / 32, 31.25, 0.03),
    "ZN": ContractSpec("10-Year T-Note", "CBOT", 1000, 1 / 64, 15.625, 0.02),
    "ZF": ContractSpec("5-Year T-Note", "CBOT", 1000, 1 / 128, 7.8125, 0.015),
    "ZT": ContractSpec("2-Year T-Note", "CBOT", 2000, 1 / 128, 15.625, 0.01),
}

# ── FX Spread Cost Model (pips) ──────────────────────────────

FX_SPREAD_PIPS: dict[str, float] = {
    # G10 majors
    "EURUSD": 0.5,
    "GBPUSD": 0.7,
    "USDJPY": 0.5,
    "USDCHF": 0.8,
    "AUDUSD": 0.7,
    "USDCAD": 0.8,
    "NZDUSD": 1.0,
    # G10 crosses
    "EURGBP": 0.8,
    "EURJPY": 0.7,
    "GBPJPY": 1.2,
    "AUDJPY": 1.0,
    "EURAUD": 1.2,
    "EURCHF": 1.0,
    "GBPCHF": 1.5,
    "CADJPY": 1.2,
    "AUDCAD": 1.2,
    "AUDNZD": 1.5,
    "NZDJPY": 1.2,
    # EM pairs — wider spreads
    "USDMXN": 3.0,
    "USDZAR": 5.0,
    "USDTRY": 8.0,
    "USDBRL": 5.0,
    "USDINR": 4.0,
    "USDCNY": 3.0,
    "USDSGD": 1.5,
    "USDHKD": 1.0,
    "USDKRW": 5.0,
    "USDTWD": 4.0,
    "USDTHB": 4.0,
    "USDIDR": 8.0,
    "USDPLN": 3.0,
    "USDCZK": 3.0,
    "USDSEK": 2.0,
    "USDNOK": 2.0,
    "USDDKK": 1.5,
}

# ── Crypto Fee Model ─────────────────────────────────────────

CRYPTO_TAKER_FEE_PCT = 0.001  # 0.1% per side (Coinbase/Binance standard)
CRYPTO_MAKER_FEE_PCT = 0.0006  # 0.06% maker
CRYPTO_SPREAD_PCT = 0.0005  # 0.05% slippage estimate


# ── Cost Functions ────────────────────────────────────────────


def fx_pip_size(pair: str) -> float:
    """Return pip size for a currency pair (0.0001 for most, 0.01 for JPY)."""
    return 0.01 if "JPY" in pair else 0.0001


def fx_cost(pair: str, lot_size: float, holding_days: float = 0) -> float:
    """Calculate round-trip FX transaction cost.

    Args:
        pair: Currency pair (e.g. "EURUSD")
        lot_size: Number of standard lots (1 lot = 100,000 units)
        holding_days: Days held (for swap/financing cost)

    Returns:
        Total cost in USD
    """
    spread = FX_SPREAD_PIPS.get(pair, 2.0)
    pip_sz = fx_pip_size(pair)
    # Spread cost: 2 sides × spread_pips × pip_value × lot_size
    # pip_value for 1 standard lot ≈ pip_size × 100,000
    notional = lot_size * 100_000
    spread_cost = 2 * spread * pip_sz * notional
    # Overnight financing (swap): ~0.005% per day per side
    swap_cost = holding_days * 0.00005 * notional
    return spread_cost + swap_cost


def commodity_cost(
    symbol: str, lots: int, entry_price: float, exit_price: float
) -> float:
    """Round-trip cost for a commodity futures position."""
    spec = COMMODITY_CONTRACT_SPECS.get(symbol)
    if spec is None:
        # Fallback: generic futures cost
        return lots * 5.0  # $2.50/side × 2
    # Commission: $2.50/contract/side for retail
    commission = lots * 5.0
    # Slippage: 0.5-2 ticks per side
    slippage_entry = spec.tick_value * 1.0 * lots
    slippage_exit = spec.tick_value * 1.0 * lots
    return commission + slippage_entry + slippage_exit


def rate_cost(symbol: str, lots: int) -> float:
    """Round-trip cost for a rate/bond futures position."""
    spec = RATE_CONTRACT_SPECS.get(symbol)
    if spec is None:
        return lots * 5.0
    commission = lots * 5.0
    slippage = spec.tick_value * 1.0 * lots * 2  # 1 tick each side
    return commission + slippage


def crypto_cost(notional: float) -> float:
    """Round-trip cost for a crypto position."""
    # Taker fee both sides + spread
    fee = notional * CRYPTO_TAKER_FEE_PCT * 2
    spread = notional * CRYPTO_SPREAD_PCT * 2
    return fee + spread
