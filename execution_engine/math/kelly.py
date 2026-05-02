"""Multivariate Volatility Regulated Kelly Criterion position sizing.

Implements the continuous-finance derivation from the architecture doc:
  f* = (1/λ) · Σ⁻¹ · (μ - r_f)

Then applies Half-Kelly constraint for behavioral ruin protection.
"""

from __future__ import annotations

import numpy as np

from shared.config import settings
from shared.constants import DEFAULT_RISK_FREE_RATE
from shared.logger import get_logger

log = get_logger("math.kelly")

_RISK_FREE_RATE = DEFAULT_RISK_FREE_RATE


def kelly_fraction_single(
    win_probability: float,
    win_loss_ratio: float,
) -> float:
    """Classic Kelly Criterion for a single binary bet.

    f* = p - (1-p)/b
    where p = win probability, b = win/loss ratio.

    **P1-Y input hardening.** Defensive clamps on both inputs:
      * ``win_probability`` is forced into ``[0, 1]`` — a miscalibrated
        upstream that passed 1.3 would otherwise yield f* > 1 before the
        cap, risking leverage after downstream cost-model scaling.
        NaN / inf inputs collapse to 0 (no bet).
      * ``win_loss_ratio <= 0`` returns 0 (undefined payoff) and NaN / inf
        also collapses to 0 rather than propagating through f*.
    The cap via ``MAX_POSITION_PCT / 100`` is the hard ceiling; the
    clamps above make the cap necessary-and-sufficient instead of
    merely best-effort.
    """
    import math

    if (
        not math.isfinite(win_probability)
        or not math.isfinite(win_loss_ratio)
        or win_loss_ratio <= 0.0
    ):
        return 0.0
    p = max(0.0, min(1.0, float(win_probability)))
    q = 1.0 - p
    f_star = p - (q / win_loss_ratio)
    half_kelly = f_star * settings.KELLY_FRACTION
    return max(0.0, min(half_kelly, settings.MAX_POSITION_PCT / 100.0))


def _ledoit_wolf_shrink(cov: np.ndarray, intensity: float | None = None) -> np.ndarray:
    """Apply Ledoit-Wolf-style shrinkage toward a scaled identity target.

    Sample covariance with few observations is poorly conditioned and
    biased toward the directions of recent realised noise — Kelly's
    plug-in inverse then recommends very large positions in directions
    that look high-Sharpe purely by chance. Shrinking toward a
    diagonal target with the average sample variance pulls those
    directions back toward isotropy.

    ``intensity`` (0 = no shrinkage, 1 = pure target) is the Ledoit-Wolf
    optimum when set automatically; here we use a conservative fixed
    0.30 by default which is close to optimal for the typical AYVID
    universe size (5–20 names, 30–60 day lookback). Pass an explicit
    value to override.
    """
    n = cov.shape[0]
    if n == 0:
        return cov
    if intensity is None:
        intensity = 0.30
    intensity = float(np.clip(intensity, 0.0, 1.0))
    avg_var = float(np.mean(np.diag(cov)))
    target = avg_var * np.eye(n)
    return (1.0 - intensity) * cov + intensity * target


def multivariate_kelly(
    expected_returns: np.ndarray,
    covariance_matrix: np.ndarray,
    risk_free_rate: float = _RISK_FREE_RATE,
    aversion_multiplier: float = 2.0,
    *,
    shrinkage_intensity: float | None = 0.30,
    # Alias for backward compatibility — many call sites still use the
    # legacy name. The two parameters are equivalent.
    volatility_penalty: float | None = None,
) -> np.ndarray:
    """Multivariate Volatility Regulated Kelly allocation.

    f* = (1/λ) · Σ⁻¹ · (μ - r_f)

    Args:
        expected_returns: Vector of annualized expected returns (n,)
        covariance_matrix: Annualized covariance matrix (n, n). Will be
            shrunk toward a scaled-identity target if
            ``shrinkage_intensity`` > 0 — recommended on small samples.
        risk_free_rate: Risk-free rate for excess return calculation.
        aversion_multiplier: λ — penalises idiosyncratic risk. The
            effective Kelly fraction returned is then further scaled by
            ``settings.KELLY_FRACTION`` (typically 0.5). Combined, the
            default behaviour is approximately quarter-Kelly (1/4),
            which is more conservative than the unscaled formula and
            mitigates the well-known plug-in over-betting bias.
        shrinkage_intensity: Ledoit-Wolf shrinkage weight on Σ. Set to
            ``None`` or 0 to disable (legacy behaviour). 0.30 is a safe
            default for the 5–20 name × 30–60 day regime AYVID runs in.
        volatility_penalty: deprecated alias for ``aversion_multiplier``.

    Returns:
        Allocation vector as fraction of portfolio.
    """
    if volatility_penalty is not None:
        aversion_multiplier = volatility_penalty
    n = len(expected_returns)
    if n == 0:
        return np.array([])

    excess_returns = expected_returns - risk_free_rate

    # Ledoit-Wolf shrinkage BEFORE ridge — corrects estimation error in
    # Σ rather than just preventing numerical singularity.
    shrunk = (
        _ledoit_wolf_shrink(covariance_matrix, intensity=shrinkage_intensity)
        if shrinkage_intensity
        else covariance_matrix
    )
    # Diagonal regularization: add a small ridge term to prevent
    # near-singular covariance from producing explosive allocations.
    ridge = 1e-6 * np.eye(n)
    cov_reg = shrunk + ridge
    try:
        cov_inv = np.linalg.inv(cov_reg)
    except np.linalg.LinAlgError:
        log.warning("Singular covariance matrix — using pseudo-inverse")
        cov_inv = np.linalg.pinv(cov_reg)

    raw_allocation = (1.0 / aversion_multiplier) * cov_inv @ excess_returns

    # NaN / inf guard: if any allocation is non-finite, zero the vector.
    if not np.all(np.isfinite(raw_allocation)):
        log.warning("Non-finite Kelly allocation detected — zeroing")
        return np.zeros(n)

    half_kelly = raw_allocation * settings.KELLY_FRACTION

    max_per_position = settings.MAX_POSITION_PCT / 100.0
    clamped = np.clip(half_kelly, -max_per_position, max_per_position)

    total = np.sum(np.abs(clamped))
    if total > 1.0:
        clamped = clamped / total

    log.info(
        f"Kelly allocation: {dict(enumerate(clamped.tolist()))}, "
        f"total exposure: {np.sum(np.abs(clamped)):.2%}"
    )
    return clamped


def compute_position_size(
    portfolio_equity: float,
    allocation_fraction: float,
    current_price: float,
) -> int:
    """Convert a Kelly allocation fraction into a share count."""
    if current_price <= 0 or allocation_fraction == 0:
        return 0
    dollar_amount = portfolio_equity * abs(allocation_fraction)
    shares = int(dollar_amount / current_price)
    return max(shares, 0)


def _conviction_size_multiplier(conviction: float) -> float:
    """Batch-1/D: scale size linearly in the region where the supervisor
    allows a trade — below floor → 0, at floor → 0, at 1.0 → 1.0.

    The fraction is defined as::

        m = (conviction - SUPERVISOR_MIN_CONFIDENCE)
            / (1.0 - SUPERVISOR_MIN_CONFIDENCE)

    clamped to ``[0, 1]``. A signal at exactly the supervisor floor
    carries zero size; a maxed-out 1.0 conviction carries full size.
    Intermediate convictions get proportional size. This is the
    institutional pattern: size scales with edge, not with presence.

    Separate from the ``win_probability`` input to
    ``kelly_fraction_single`` — that drives the raw Kelly bet; this
    scales the final Kelly fraction so a 0.62 signal gets a much
    smaller position than a 0.95 signal even when both clear
    ``win_probability > 0.5``.
    """
    import math

    if not math.isfinite(conviction):
        return 0.0
    floor = float(settings.SUPERVISOR_MIN_CONFIDENCE)
    span = 1.0 - floor
    if span <= 0.0:
        return 1.0 if conviction >= floor else 0.0
    raw = (float(conviction) - floor) / span
    return max(0.0, min(1.0, raw))


def optimal_allocation_for_signal(
    portfolio_equity: float,
    win_probability: float,
    expected_return: float,
    volatility: float,
    current_price: float,
    enforce_ev_hurdle: bool = True,
    conviction: float | None = None,
    asset_class: str = "EQUITY",
) -> tuple[float, int]:
    """Convenience: compute Kelly fraction and share count for a single signal.

    When `enforce_ev_hurdle` is True (default), the resulting share count
    is zeroed out if the expected PnL does not clear 4× the modeled
    round-trip execution cost (commission + bid/ask slippage). This is
    the Tier-1 institutional filter that kills sub-noise trades before
    they reach the execution tier.

    Batch-1/D: when ``conviction`` is provided, the final Kelly fraction
    is multiplied by a conviction-scaled size multiplier
    (``_conviction_size_multiplier``). A barely-clearing 0.62 signal
    then gets ~12% of the size a 0.95 signal would get, preserving
    capital for the higher-edge opportunities. Callers that don't
    pass ``conviction`` get the legacy unscaled behaviour.

    Returns (allocation_fraction, share_count).
    """
    if volatility <= 0:
        volatility = 0.2

    win_loss_ratio = max(abs(expected_return / volatility), 0.1)
    frac = kelly_fraction_single(win_probability, win_loss_ratio)

    if conviction is not None:
        multiplier = _conviction_size_multiplier(conviction)
        frac_scaled = frac * multiplier
        if frac > 0 and multiplier < 1.0:
            log.info(
                f"Conviction-scaled sizing: conviction={conviction:.2f} "
                f"multiplier={multiplier:.2f} frac {frac:.4f} → {frac_scaled:.4f}"
            )
        frac = frac_scaled

    shares = compute_position_size(portfolio_equity, frac, current_price)

    if enforce_ev_hurdle and shares > 0:
        from execution_engine.math.costs import evaluate_trade_ev

        report = evaluate_trade_ev(
            asset_class=asset_class,
            expected_return_pct=abs(expected_return),
            price=current_price,
            notional_shares=shares,
            label=f"kelly_{asset_class.lower()}",
        )
        if not report.passes:
            log.info(
                f"[EV-GATE] killed equity sizing: {report.reason} "
                f"(shares={shares}, price=${current_price:.2f})"
            )
            return frac, 0

    log.info(
        f"Signal sizing: frac={frac:.4f}, shares={shares}, notional=${shares * current_price:,.0f}"
    )
    return frac, shares


# ── F&O-Aware Kelly Sizing ───────────────────────────────────


def kelly_options(
    win_probability: float,
    expected_payoff: float,
    max_loss: float,
    premium_cost: float,
) -> float:
    """Kelly fraction for options trades where payoff is non-linear.

    For defined-risk strategies (spreads, condors), max_loss is known.
    Fraction is sized relative to the risk capital (premium or margin),
    not the notional.
    """
    import math

    if (
        not math.isfinite(win_probability)
        or not math.isfinite(expected_payoff)
        or not math.isfinite(max_loss)
        or not math.isfinite(premium_cost)
    ):
        return 0.0
    if max_loss <= 0 or premium_cost <= 0:
        return 0.0

    # Clamp probability to [0, 1]
    p = max(0.0, min(1.0, float(win_probability)))

    expected_profit = expected_payoff - premium_cost
    if expected_profit <= 0:
        return 0.0

    rr = expected_profit / max_loss
    q = 1.0 - p
    f_star = p - (q / rr) if rr > 0 else 0.0
    # Edge check: negative Kelly means no edge — don't bet
    if f_star <= 0:
        return 0.0
    half_kelly = f_star * settings.KELLY_FRACTION
    return max(0.0, min(half_kelly, settings.MAX_POSITION_PCT / 100.0))


def compute_contracts_size(
    portfolio_equity: float,
    allocation_fraction: float,
    premium_per_contract: float,
    multiplier: float = 100.0,
) -> int:
    """Convert a Kelly allocation into a contract count for options/futures.

    Unlike equity sizing (dollars / price = shares), derivatives use
    (dollars / (premium * multiplier)) = contracts.
    """
    if premium_per_contract <= 0 or allocation_fraction <= 0:
        return 0
    dollar_amount = portfolio_equity * allocation_fraction
    cost_per_contract = premium_per_contract * multiplier
    return max(int(dollar_amount / cost_per_contract), 0)


def kelly_futures(
    win_probability: float,
    avg_win_ticks: float,
    avg_loss_ticks: float,
    tick_value: float,
    margin_per_contract: float,
    portfolio_equity: float,
) -> int:
    """Kelly-optimal number of futures contracts.

    Sizes based on margin consumption rather than notional.
    """
    import math

    if (
        not math.isfinite(win_probability)
        or not math.isfinite(avg_win_ticks)
        or not math.isfinite(avg_loss_ticks)
    ):
        return 0
    if avg_loss_ticks <= 0 or margin_per_contract <= 0:
        return 0

    p = max(0.0, min(1.0, float(win_probability)))
    rr = avg_win_ticks / avg_loss_ticks
    q = 1.0 - p
    f_star = p - (q / rr) if rr > 0 else 0.0

    if f_star <= 0:
        return 0

    half_kelly = f_star * settings.KELLY_FRACTION
    risk_capital = portfolio_equity * min(half_kelly, settings.MAX_POSITION_PCT / 100.0)
    return max(int(risk_capital / margin_per_contract), 0)
