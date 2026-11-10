"""Deterministic research arithmetic.

Every derived number shown in the workspace comes from here — never from an
LLM. Money ratios use ``Decimal``; market statistics reuse the canonical
NumPy primitives in ``shared.quant._primitives``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, getcontext
from typing import Any

import numpy as np

from shared.quant._primitives import compute_hurst, compute_realized_vol, log_returns

getcontext().prec = 28
PCT = Decimal("100")
TWO_DP = Decimal("0.01")
VOL_WINDOW_BARS = 90
RETURN_WINDOW_BARS = 90


class CalculationError(ValueError):
    """Inputs are not comparable (period / currency / scale mismatch, zero base)."""


@dataclass(frozen=True)
class Calc:
    """An auditable calculation: formula + named inputs + deterministic output."""

    calc_id: str
    label: str
    formula: str
    inputs: tuple[dict[str, Any], ...]
    result: Decimal
    unit: str
    assumptions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "calc_id": self.calc_id,
            "label": self.label,
            "formula": self.formula,
            "inputs": list(self.inputs),
            "result": str(self.result),
            "unit": self.unit,
            "assumptions": list(self.assumptions),
        }


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def _input(obs: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": obs["metric_label"],
        "metric_code": obs["metric_code"],
        "value": str(obs["value"]),
        "currency": obs.get("currency"),
        "scale": obs["scale"],
        "fiscal_period": obs["fiscal_period"],
        "source_id": obs.get("source_id"),
        "observation_id": obs.get("observation_id"),
        "as_of": obs.get("as_of"),
    }


def assert_comparable(a: dict[str, Any], b: dict[str, Any], *, same_period: bool = True) -> None:
    if same_period and a["fiscal_period"] != b["fiscal_period"]:
        raise CalculationError(
            f"fiscal period mismatch: {a['fiscal_period']} vs {b['fiscal_period']}"
        )
    if a.get("currency") != b.get("currency"):
        raise CalculationError(f"currency mismatch: {a.get('currency')} vs {b.get('currency')}")
    if a["scale"] != b["scale"]:
        raise CalculationError(f"scale mismatch: {a['scale']} vs {b['scale']}")


def ratio_pct(calc_id: str, label: str, numerator: dict[str, Any], denominator: dict[str, Any]) -> Calc:
    """``numerator / denominator × 100`` for two same-period, same-currency observations."""
    assert_comparable(numerator, denominator)
    den = dec(denominator["value"])
    if den == 0:
        raise CalculationError("denominator is zero")
    result = q2(dec(numerator["value"]) / den * PCT)
    return Calc(
        calc_id=calc_id,
        label=label,
        formula=f"{numerator['metric_label']} ÷ {denominator['metric_label']} × 100",
        inputs=(_input(numerator), _input(denominator)),
        result=result,
        unit="%",
    )


def growth_pct(calc_id: str, label: str, current: dict[str, Any], prior: dict[str, Any]) -> Calc:
    """Year-over-year growth between two periods of the same metric/currency."""
    assert_comparable(current, prior, same_period=False)
    if current["metric_code"] != prior["metric_code"]:
        raise CalculationError("growth requires the same metric")
    base = dec(prior["value"])
    if base == 0:
        raise CalculationError("prior-period value is zero")
    result = q2((dec(current["value"]) - base) / base * PCT)
    return Calc(
        calc_id=calc_id,
        label=label,
        formula=f"({current['fiscal_period']} − {prior['fiscal_period']}) ÷ {prior['fiscal_period']} × 100",
        inputs=(_input(current), _input(prior)),
        result=result,
        unit="%",
    )


# ── Market statistics from OHLCV ──────────────────────────────────────


def market_stats(closes: list[float]) -> dict[str, float | None]:
    """Close, 90-bar return %, 90-bar annualised realised vol %, max drawdown %, Hurst."""
    arr = np.asarray(closes, dtype=float)
    if arr.size < 2:
        return {"close": float(arr[-1]) if arr.size else None, "ret_90d": None,
                "vol_90d": None, "max_dd": None, "hurst": None}
    rets = log_returns(arr)
    ret_90 = (
        float((arr[-1] / arr[-RETURN_WINDOW_BARS - 1] - 1) * 100)
        if arr.size > RETURN_WINDOW_BARS
        else None
    )
    vol = compute_realized_vol(rets, window=VOL_WINDOW_BARS) * 100 if rets.size >= VOL_WINDOW_BARS else None
    running_peak = np.maximum.accumulate(arr)
    max_dd = float(((arr - running_peak) / running_peak).min() * 100)
    hurst = compute_hurst(rets) if rets.size >= 40 else None
    return {"close": float(arr[-1]), "ret_90d": ret_90, "vol_90d": vol, "max_dd": max_dd, "hurst": hurst}


# ── Freshness ──────────────────────────────────────────────────────────


def staleness(as_of: str | None, threshold_days: int, today: date | None = None) -> dict[str, Any]:
    if not as_of:
        return {"is_stale": True, "age_days": None, "as_of": None}
    today = today or datetime.now(UTC).date()
    as_of_date = date.fromisoformat(as_of[:10])
    age = (today - as_of_date).days
    return {"is_stale": age > threshold_days, "age_days": age, "as_of": as_of_date.isoformat()}


# ── DCF sandbox ────────────────────────────────────────────────────────


def dcf(params: dict[str, Any]) -> dict[str, Any]:
    """Simple FCFF DCF with Gordon terminal value. All inputs labelled ASSUMED."""
    revenue = dec(params["base_revenue"])
    g = dec(params["revenue_growth_pct"]) / PCT
    margin = dec(params["operating_margin_pct"]) / PCT
    tax = dec(params.get("tax_rate_pct", 15)) / PCT
    reinvest = dec(params.get("reinvestment_pct_of_nopat", 30)) / PCT
    r = dec(params["discount_rate_pct"]) / PCT
    tg = dec(params.get("terminal_growth_pct", 3)) / PCT
    years = int(params.get("years", 5))
    if r <= tg:
        raise CalculationError("discount rate must exceed terminal growth")

    rows = []
    pv_sum = Decimal(0)
    fcf = Decimal(0)
    for year in range(1, years + 1):
        revenue = revenue * (1 + g)
        nopat = revenue * margin * (1 - tax)
        fcf = nopat * (1 - reinvest)
        pv = fcf / (1 + r) ** year
        pv_sum += pv
        rows.append({"year": year, "revenue": str(q2(revenue)), "fcf": str(q2(fcf)), "pv": str(q2(pv))})
    terminal = fcf * (1 + tg) / (r - tg)
    pv_terminal = terminal / (1 + r) ** years
    ev = pv_sum + pv_terminal
    equity = ev + dec(params.get("net_cash", 0))
    shares = params.get("shares_outstanding")
    per_share = q2(equity / dec(shares)) if shares else None
    return {
        "rows": rows,
        "pv_explicit": str(q2(pv_sum)),
        "pv_terminal": str(q2(pv_terminal)),
        "enterprise_value": str(q2(ev)),
        "equity_value": str(q2(equity)),
        "value_per_share": str(per_share) if per_share is not None else None,
        "terminal_share_pct": str(q2(pv_terminal / ev * PCT)) if ev else None,
        "formula": "EV = Σ FCFₜ/(1+r)ᵗ + FCFₙ(1+g)/(r−g)/(1+r)ⁿ; FCF = Rev × margin × (1−tax) × (1−reinvest)",
    }


def dcf_sensitivity(params: dict[str, Any], rates: list[float], growths: list[float]) -> list[dict[str, Any]]:
    """Equity value across a discount-rate × revenue-growth grid."""
    grid = []
    for rate in rates:
        row: dict[str, Any] = {"discount_rate_pct": rate, "values": []}
        for growth in growths:
            try:
                out = dcf({**params, "discount_rate_pct": rate, "revenue_growth_pct": growth})
                row["values"].append(out["value_per_share"] or out["equity_value"])
            except CalculationError:
                row["values"].append(None)
        grid.append(row)
    return grid
