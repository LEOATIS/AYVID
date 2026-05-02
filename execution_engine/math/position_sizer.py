"""Per-asset-class quantity / notional / margin sizing.

Every signal that hits the SignalBook needs a concrete share / contract
/ lot count plus the dollar risk it carries. The Kelly module
(``execution_engine.math.kelly``) returns a *fraction* of the portfolio
to allocate; this module turns that fraction into a tradable integer
per asset-class semantics:

  * EQUITY / ETF       — shares = floor(risk_$ / (entry - SL))
  * INDEX_FUTURE       — contracts = floor(risk_$ / (ticks_SL × tick_value))
  * FX                 — lots = round(risk_$ / (pips_SL × pip_value), 2)
  * COMMODITY_FUTURE   — contracts = floor(risk_$ / (move_$ × multiplier))
  * CRYPTO             — units = round(risk_$ / (entry - SL), 6)
  * OPTION             — premium-based, sized by max-loss not move

The return contract is a single ``PositionSize`` dataclass so every
caller sees the same shape; downstream serialisation into
``PrecomputedSignal`` is a one-line copy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shared.logger import get_logger

log = get_logger("math.position_sizer")


# ── Defaults ────────────────────────────────────────────────────

DEFAULT_RISK_PER_TRADE_PCT = 0.02   # 2% of portfolio at risk per trade
CRYPTO_RISK_SCALAR = 0.5            # halve crypto risk vs equity
CRYPTO_MAX_NOTIONAL_PCT = 0.01      # cap per-name crypto notional at 1% of book
DEFAULT_PORTFOLIO_VALUE = 1_000_000.0


# Tick / multiplier specs for the futures we trade. These mirror the
# constants in ``graph_intelligence.agents.strategy_orchestrator
# .INDEX_FUTURES_SPECS`` — kept here in a flatter shape so the sizer
# never imports the orchestrator (cycle-free).
_INDEX_FUTURE_SPECS: dict[str, dict[str, float]] = {
    "ES":    {"tick_size": 0.25, "tick_value": 12.50, "multiplier": 50.0,  "margin_pct": 0.05},
    "NQ":    {"tick_size": 0.25, "tick_value": 5.00,  "multiplier": 20.0,  "margin_pct": 0.05},
    "YM":    {"tick_size": 1.00, "tick_value": 5.00,  "multiplier": 5.0,   "margin_pct": 0.05},
    "RTY":   {"tick_size": 0.10, "tick_value": 5.00,  "multiplier": 50.0,  "margin_pct": 0.05},
    "NIFTY": {"tick_size": 0.05, "tick_value": 3.75,  "multiplier": 75.0,  "margin_pct": 0.10},
}

_COMMODITY_FUTURE_SPECS: dict[str, dict[str, float]] = {
    "CL":  {"tick_size": 0.01, "tick_value": 10.0, "multiplier": 1000.0, "margin_pct": 0.07},
    "GC":  {"tick_size": 0.10, "tick_value": 10.0, "multiplier": 100.0,  "margin_pct": 0.05},
    "SI":  {"tick_size": 0.005, "tick_value": 25.0, "multiplier": 5000.0, "margin_pct": 0.07},
    "NG":  {"tick_size": 0.001, "tick_value": 10.0, "multiplier": 10000.0, "margin_pct": 0.10},
    "HG":  {"tick_size": 0.0005, "tick_value": 12.5, "multiplier": 25000.0, "margin_pct": 0.07},
    "ZC":  {"tick_size": 0.0025, "tick_value": 12.5, "multiplier": 5000.0, "margin_pct": 0.05},
    "ZS":  {"tick_size": 0.0025, "tick_value": 12.5, "multiplier": 5000.0, "margin_pct": 0.05},
    "ZW":  {"tick_size": 0.0025, "tick_value": 12.5, "multiplier": 5000.0, "margin_pct": 0.05},
}

# FX: standard lot = 100k base-currency units. Pip value below is per
# standard lot (USD-quoted majors). Crosses use a quote-currency
# adjustment that we don't model explicitly — close enough for sizing.
_FX_PIP_SIZE_NON_JPY = 0.0001
_FX_PIP_SIZE_JPY = 0.01
_FX_STANDARD_LOT_UNITS = 100_000.0
_FX_PIP_VALUE_PER_STD_LOT = 10.0


# ── Public dataclass ────────────────────────────────────────────


@dataclass
class PositionSize:
    """The shape every sizer returns. Mirrors the new fields on
    ``PrecomputedSignal`` — copy directly into the signal record.
    """

    quantity: float = 0.0           # shares / contracts / lots / units
    notional_value: float = 0.0     # quantity × entry_price (or × multiplier)
    risk_amount: float = 0.0        # $ at risk if SL is hit
    kelly_fraction: float = 0.0     # the Kelly fraction this size came from
    lot_size: float | None = None
    contract_multiplier: float | None = None
    pip_value: float | None = None
    margin_required: float | None = None
    rationale: str = ""             # one-line explanation for logs


# ── Helpers ─────────────────────────────────────────────────────


def _safe_float(value: float | None, default: float = 0.0) -> float:
    if value is None or not math.isfinite(value):
        return default
    return float(value)


def _is_jpy_pair(ticker: str) -> bool:
    t = ticker.upper().replace("/", "").replace("-", "")
    return "JPY" in t


def _root_future(ticker: str) -> str:
    """Strip a contract month off a futures ticker.

    ``ESH4`` → ``ES``, ``NQ`` → ``NQ``, ``CL!`` → ``CL``. We are not
    parsing a full CME spec here — just stripping the trailing
    month-letter / digit combo so a lookup against
    ``_INDEX_FUTURE_SPECS`` works for both root and front-month
    notation.
    """
    raw = ticker.upper().lstrip("^").rstrip("!")
    # Walk back from the end while we see digits / single-letter
    # month codes.
    i = len(raw)
    while i > 2 and (raw[i - 1].isdigit() or raw[i - 1] in "FGHJKMNQUVXZ"):
        i -= 1
    return raw[:i] or raw


# ── Per-asset sizers ────────────────────────────────────────────


def size_equity(
    *,
    entry_price: float,
    stop_loss_price: float,
    portfolio_value: float,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    kelly_fraction: float = 0.0,
) -> PositionSize:
    """Equity / ETF sizing.

    risk_per_trade = portfolio × risk_pct
    quantity       = floor(risk_per_trade / |entry − SL|)
    """
    risk_per_trade = max(portfolio_value * risk_per_trade_pct, 0.0)
    risk_per_share = abs(entry_price - stop_loss_price)
    if risk_per_share <= 0 or entry_price <= 0:
        return PositionSize(
            kelly_fraction=kelly_fraction,
            rationale="equity: invalid SL distance",
        )
    qty = math.floor(risk_per_trade / risk_per_share)
    qty = max(qty, 0)
    notional = qty * entry_price
    risk_amount = qty * risk_per_share
    return PositionSize(
        quantity=float(qty),
        notional_value=round(notional, 2),
        risk_amount=round(risk_amount, 2),
        kelly_fraction=kelly_fraction,
        rationale=(
            f"equity: risk_$={risk_per_trade:.0f}, "
            f"per_share={risk_per_share:.2f} → {qty} shares"
        ),
    )


def size_index_future(
    *,
    ticker: str,
    entry_price: float,
    stop_loss_price: float,
    portfolio_value: float,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    kelly_fraction: float = 0.0,
) -> PositionSize:
    """Index-future sizing — contracts via tick × tick_value risk."""
    spec = _INDEX_FUTURE_SPECS.get(_root_future(ticker)) or _INDEX_FUTURE_SPECS.get(ticker.upper())
    risk_per_trade = max(portfolio_value * risk_per_trade_pct, 0.0)
    risk_per_unit = abs(entry_price - stop_loss_price)
    if not spec or risk_per_unit <= 0 or entry_price <= 0:
        return PositionSize(
            kelly_fraction=kelly_fraction,
            rationale=f"index_future: missing spec for {ticker}",
        )
    tick_size = spec["tick_size"]
    tick_value = spec["tick_value"]
    multiplier = spec["multiplier"]
    margin_pct = spec.get("margin_pct", 0.05)
    ticks_sl = max(risk_per_unit / tick_size, 1.0)
    risk_per_contract = ticks_sl * tick_value
    contracts = max(math.floor(risk_per_trade / risk_per_contract), 1)
    notional = contracts * entry_price * multiplier
    margin_required = notional * margin_pct
    return PositionSize(
        quantity=float(contracts),
        notional_value=round(notional, 2),
        risk_amount=round(contracts * risk_per_contract, 2),
        kelly_fraction=kelly_fraction,
        contract_multiplier=multiplier,
        margin_required=round(margin_required, 2),
        rationale=(
            f"index_future {ticker}: ticks_sl={ticks_sl:.0f}, "
            f"per_contract=${risk_per_contract:.0f} → {contracts} contracts, "
            f"margin=${margin_required:.0f}"
        ),
    )


def size_commodity_future(
    *,
    ticker: str,
    entry_price: float,
    stop_loss_price: float,
    portfolio_value: float,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    kelly_fraction: float = 0.0,
) -> PositionSize:
    """Commodity future sizing — contracts via price-move × multiplier risk."""
    spec = _COMMODITY_FUTURE_SPECS.get(_root_future(ticker)) or _COMMODITY_FUTURE_SPECS.get(ticker.upper())
    risk_per_trade = max(portfolio_value * risk_per_trade_pct, 0.0)
    risk_per_unit = abs(entry_price - stop_loss_price)
    if not spec or risk_per_unit <= 0 or entry_price <= 0:
        # Fall back to equity-style sizing — better than zero and the
        # cost-model layer will gate it via the EV check anyway.
        fallback = size_equity(
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
        fallback.rationale = f"commodity_future {ticker}: no spec — fell back to equity sizing"
        return fallback
    multiplier = spec["multiplier"]
    margin_pct = spec.get("margin_pct", 0.07)
    risk_per_contract = risk_per_unit * multiplier
    contracts = max(math.floor(risk_per_trade / risk_per_contract), 1)
    notional = contracts * entry_price * multiplier
    margin_required = notional * margin_pct
    return PositionSize(
        quantity=float(contracts),
        notional_value=round(notional, 2),
        risk_amount=round(contracts * risk_per_contract, 2),
        kelly_fraction=kelly_fraction,
        contract_multiplier=multiplier,
        margin_required=round(margin_required, 2),
        rationale=(
            f"commodity_future {ticker}: per_contract=${risk_per_contract:.0f} "
            f"→ {contracts} contracts"
        ),
    )


def size_fx(
    *,
    ticker: str,
    entry_price: float,
    stop_loss_price: float,
    portfolio_value: float,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    kelly_fraction: float = 0.0,
) -> PositionSize:
    """FX sizing — lots via pips × pip_value risk."""
    risk_per_trade = max(portfolio_value * risk_per_trade_pct, 0.0)
    pip_size = _FX_PIP_SIZE_JPY if _is_jpy_pair(ticker) else _FX_PIP_SIZE_NON_JPY
    risk_per_unit = abs(entry_price - stop_loss_price)
    if pip_size <= 0 or risk_per_unit <= 0 or entry_price <= 0:
        return PositionSize(
            kelly_fraction=kelly_fraction,
            rationale=f"fx {ticker}: invalid pip_size / SL distance",
        )
    pips_sl = risk_per_unit / pip_size
    risk_per_lot = max(pips_sl * _FX_PIP_VALUE_PER_STD_LOT, 1e-6)
    lots = round(risk_per_trade / risk_per_lot, 2)
    lots = max(lots, 0.0)
    notional = lots * _FX_STANDARD_LOT_UNITS * entry_price
    return PositionSize(
        quantity=lots,
        notional_value=round(notional, 2),
        risk_amount=round(lots * risk_per_lot, 2),
        kelly_fraction=kelly_fraction,
        lot_size=_FX_STANDARD_LOT_UNITS,
        pip_value=_FX_PIP_VALUE_PER_STD_LOT,
        rationale=(
            f"fx {ticker}: pips_sl={pips_sl:.0f}, per_lot=${risk_per_lot:.0f} "
            f"→ {lots:.2f} lots"
        ),
    )


def size_crypto(
    *,
    ticker: str,
    entry_price: float,
    stop_loss_price: float,
    portfolio_value: float,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    kelly_fraction: float = 0.0,
) -> PositionSize:
    """Crypto sizing — units with a 0.5× risk scalar and a 1% notional cap."""
    risk_per_trade = max(portfolio_value * risk_per_trade_pct * CRYPTO_RISK_SCALAR, 0.0)
    risk_per_unit = abs(entry_price - stop_loss_price)
    if risk_per_unit <= 0 or entry_price <= 0:
        return PositionSize(
            kelly_fraction=kelly_fraction,
            rationale=f"crypto {ticker}: invalid SL distance",
        )
    raw_qty = risk_per_trade / risk_per_unit
    # Cap per-name crypto exposure at CRYPTO_MAX_NOTIONAL_PCT of book.
    max_notional = portfolio_value * CRYPTO_MAX_NOTIONAL_PCT
    if raw_qty * entry_price > max_notional:
        raw_qty = max_notional / entry_price
    qty = round(raw_qty, 6)
    notional = qty * entry_price
    return PositionSize(
        quantity=qty,
        notional_value=round(notional, 2),
        risk_amount=round(qty * risk_per_unit, 2),
        kelly_fraction=kelly_fraction,
        rationale=(
            f"crypto {ticker}: risk_$={risk_per_trade:.0f}, "
            f"per_unit=${risk_per_unit:.4f} → {qty} units"
        ),
    )


def size_option(
    *,
    ticker: str,
    entry_price: float,           # premium per contract
    portfolio_value: float,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    kelly_fraction: float = 0.0,
    multiplier: float = 100.0,
    max_loss_per_contract: float | None = None,
) -> PositionSize:
    """Option sizing — contracts via max-loss / premium.

    For DEFINED-RISK structures (verticals, condors) callers should
    pass ``max_loss_per_contract`` explicitly; for naked long
    options it equals the premium paid.
    """
    risk_per_trade = max(portfolio_value * risk_per_trade_pct, 0.0)
    max_loss = max_loss_per_contract or entry_price
    if max_loss <= 0 or entry_price <= 0:
        return PositionSize(
            kelly_fraction=kelly_fraction,
            rationale=f"option {ticker}: invalid premium / max_loss",
        )
    cost_per_contract = max_loss * multiplier
    contracts = max(math.floor(risk_per_trade / cost_per_contract), 1)
    notional = contracts * entry_price * multiplier
    return PositionSize(
        quantity=float(contracts),
        notional_value=round(notional, 2),
        risk_amount=round(contracts * cost_per_contract, 2),
        kelly_fraction=kelly_fraction,
        contract_multiplier=multiplier,
        margin_required=round(contracts * cost_per_contract, 2),
        rationale=(
            f"option {ticker}: cost_per_contract=${cost_per_contract:.0f} "
            f"→ {contracts} contracts"
        ),
    )


# ── Top-level dispatcher ────────────────────────────────────────


def compute_quantity_for_signal(
    *,
    ticker: str,
    asset_class: str,
    instrument_type: str | None,
    direction: str,
    entry_price: float,
    stop_loss_price: float,
    portfolio_value: float = DEFAULT_PORTFOLIO_VALUE,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    kelly_fraction: float = 0.0,
    max_loss_per_contract: float | None = None,
) -> PositionSize:
    """Route to the right per-asset-class sizer.

    Args:
        ticker: e.g. "NVDA", "EURUSD", "ES", "BTC".
        asset_class: high-level bucket. EQUITY / INDEX / FX / FUTURE /
            CRYPTO / ETF / OPTION / COMMODITY.
        instrument_type: optional finer-grained tag from the
            orchestrator ("INDEX_FUTURE", "COMMODITY_FUTURE",
            "INDEX_CALL"…). When supplied it overrides ``asset_class``
            for routing purposes.
        direction: "LONG" / "SHORT" / "HOLD". HOLD short-circuits to
            zero quantity — signal-only.
        entry_price / stop_loss_price: absolute prices.
        portfolio_value: NLV in USD.
        risk_per_trade_pct: e.g. 0.02 for 2%.
        kelly_fraction: passed through into the result for audit.
        max_loss_per_contract: REQUIRED for defined-risk option
            structures (vertical, iron condor); ignored otherwise.

    Returns:
        ``PositionSize`` with quantity, notional, risk_amount and the
        per-asset-class context fields populated.
    """
    if str(direction).upper() == "HOLD":
        return PositionSize(
            kelly_fraction=kelly_fraction,
            rationale=f"{ticker}: HOLD signal — no quantity",
        )
    if entry_price <= 0:
        return PositionSize(
            kelly_fraction=kelly_fraction,
            rationale=f"{ticker}: missing entry_price",
        )

    inst = (instrument_type or "").upper()
    ac = (asset_class or "EQUITY").upper()

    # Instrument-level routing first (more specific than asset-class).
    if inst in ("INDEX_FUTURE", "RATE_FUTURE"):
        return size_index_future(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
    if inst == "COMMODITY_FUTURE":
        return size_commodity_future(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
    if inst == "FX":
        return size_fx(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
    if inst == "CRYPTO":
        return size_crypto(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
    if inst in ("INDEX_CALL", "INDEX_PUT", "INDEX_SPREAD", "OPTION"):
        return size_option(
            ticker=ticker,
            entry_price=entry_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
            max_loss_per_contract=max_loss_per_contract,
        )

    # Asset-class-level routing.
    if ac == "FX":
        return size_fx(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
    if ac == "CRYPTO":
        return size_crypto(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
    if ac == "FUTURE":
        # Try index spec first, then commodity spec.
        spec = _safe_float(_INDEX_FUTURE_SPECS.get(_root_future(ticker), {}).get("multiplier"))
        if spec > 0:
            return size_index_future(
                ticker=ticker,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                portfolio_value=portfolio_value,
                risk_per_trade_pct=risk_per_trade_pct,
                kelly_fraction=kelly_fraction,
            )
        return size_commodity_future(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
    if ac == "COMMODITY":
        return size_commodity_future(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
        )
    if ac == "OPTION":
        return size_option(
            ticker=ticker,
            entry_price=entry_price,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_per_trade_pct,
            kelly_fraction=kelly_fraction,
            max_loss_per_contract=max_loss_per_contract,
        )

    # Default — equity sizing covers EQUITY, INDEX (cash), ETF, BOND.
    return size_equity(
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        portfolio_value=portfolio_value,
        risk_per_trade_pct=risk_per_trade_pct,
        kelly_fraction=kelly_fraction,
    )
