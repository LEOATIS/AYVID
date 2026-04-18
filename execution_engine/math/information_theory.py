"""Information-theoretic causal detection for supply chain signal validation.

Implements:
- Transfer Entropy (T_{X→Y}): directional information flow between time series
- Granger Causality (F-test): statistical test for predictive causality
- Mutual Information: non-linear dependence between variables
- Causal Strength Index: composite score combining all three

These replace naive correlation with proper causal inference.
A shock in Company A *causes* movement in Company B only if
information flows directionally from A→B with statistical significance.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from shared.logger import get_logger

log = get_logger("math.info_theory")


@dataclass
class CausalLink:
    """Quantified causal relationship between two time series."""
    source: str
    target: str
    transfer_entropy: float
    granger_f_stat: float
    granger_p_value: float
    mutual_information: float
    causal_strength: float
    is_significant: bool
    lag: int


def _discretize(series: np.ndarray, bins: int = 10) -> np.ndarray:
    """Discretize a continuous series into bins for entropy estimation."""
    if len(series) == 0:
        return np.array([], dtype=int)
    percentiles = np.linspace(0, 100, bins + 1)
    edges = np.percentile(series, percentiles)
    edges[-1] += 1e-10
    return np.digitize(series, edges[1:-1]).astype(int)


def compute_transfer_entropy(
    source: np.ndarray,
    target: np.ndarray,
    lag: int = 1,
    bins: int = 8,
) -> float:
    """Transfer entropy T_{X→Y} — measures directed information flow.

    T_{X→Y} = H(Y_t | Y_{t-1}) - H(Y_t | Y_{t-1}, X_{t-lag})

    Higher T means X provides predictive information about Y's future
    beyond what Y's own past provides.
    """
    n = min(len(source), len(target))
    if n < lag + 10:
        return 0.0

    src = _discretize(source[:n], bins)
    tgt = _discretize(target[:n], bins)

    y_present = tgt[lag:]
    y_past = tgt[lag - 1 : -1] if lag < n else tgt[:-1]
    x_past = src[:n - lag]

    min_len = min(len(y_present), len(y_past), len(x_past))
    y_present = y_present[:min_len]
    y_past = y_past[:min_len]
    x_past = x_past[:min_len]

    def _joint_counts(*arrs: np.ndarray) -> dict:
        keys = list(zip(*arrs))
        counts: dict = {}
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
        return counts

    def _entropy_from_counts(counts: dict, total: int) -> float:
        h = 0.0
        for c in counts.values():
            p = c / total
            if p > 0:
                h -= p * np.log2(p)
        return h

    total = len(y_present)

    joint_ypast = _joint_counts(y_present, y_past)
    h_y_given_ypast = 0.0
    ypast_counts: dict = {}
    for (yp, yps), c in joint_ypast.items():
        ypast_counts[yps] = ypast_counts.get(yps, 0) + c

    for (yp, yps), c in joint_ypast.items():
        p_joint = c / total
        p_cond = c / ypast_counts[yps]
        if p_cond > 0:
            h_y_given_ypast -= p_joint * np.log2(p_cond)

    joint_all = _joint_counts(y_present, y_past, x_past)
    ypast_xpast_counts: dict = {}
    for (yp, yps, xp), c in joint_all.items():
        key = (yps, xp)
        ypast_xpast_counts[key] = ypast_xpast_counts.get(key, 0) + c

    h_y_given_both = 0.0
    for (yp, yps, xp), c in joint_all.items():
        p_joint = c / total
        p_cond = c / ypast_xpast_counts[(yps, xp)]
        if p_cond > 0:
            h_y_given_both -= p_joint * np.log2(p_cond)

    te = max(h_y_given_ypast - h_y_given_both, 0.0)
    return float(te)


def compute_granger_causality(
    source: np.ndarray,
    target: np.ndarray,
    max_lag: int = 5,
) -> tuple[float, float, int]:
    """Granger causality F-test: does source predict target beyond autoregression?

    Returns (F-statistic, p-value, optimal_lag).

    Implements the VAR-based F-test:
    H0: coefficients on lagged source are all zero
    H1: at least one coefficient is non-zero
    """
    n = min(len(source), len(target))
    if n < max_lag + 20:
        return 0.0, 1.0, 1

    best_f = 0.0
    best_p = 1.0
    best_lag = 1

    for lag in range(1, max_lag + 1):
        y = target[lag:n]
        n_obs = len(y)

        X_restricted = np.column_stack([
            target[lag - j - 1 : n - j - 1] for j in range(lag)
        ])

        X_full = np.column_stack([
            X_restricted,
            *[source[lag - j - 1 : n - j - 1].reshape(-1, 1) for j in range(lag)],
        ])

        X_restricted = np.column_stack([np.ones(n_obs), X_restricted])
        X_full = np.column_stack([np.ones(n_obs), X_full])

        min_obs = min(n_obs, X_restricted.shape[0], X_full.shape[0])
        y = y[:min_obs]
        X_restricted = X_restricted[:min_obs]
        X_full = X_full[:min_obs]

        try:
            beta_r = np.linalg.lstsq(X_restricted, y, rcond=None)[0]
            resid_r = y - X_restricted @ beta_r
            ssr_r = np.sum(resid_r ** 2)

            beta_f = np.linalg.lstsq(X_full, y, rcond=None)[0]
            resid_f = y - X_full @ beta_f
            ssr_f = np.sum(resid_f ** 2)

            q = lag
            df_f = min_obs - X_full.shape[1]
            if df_f <= 0 or ssr_f <= 0:
                continue

            f_stat = ((ssr_r - ssr_f) / q) / (ssr_f / df_f)

            from scipy import stats as sp_stats
            p_value = 1.0 - sp_stats.f.cdf(f_stat, q, df_f)

            if f_stat > best_f:
                best_f = float(f_stat)
                best_p = float(p_value)
                best_lag = lag
        except (np.linalg.LinAlgError, ValueError):
            continue

    return best_f, best_p, best_lag


def compute_mutual_information(
    x: np.ndarray, y: np.ndarray, bins: int = 10,
) -> float:
    """Mutual information I(X;Y) — non-linear dependence measure.

    I(X;Y) = H(X) + H(Y) - H(X,Y)

    Unlike correlation, captures all forms of statistical dependence.
    """
    n = min(len(x), len(y))
    if n < 20:
        return 0.0

    x_d = _discretize(x[:n], bins)
    y_d = _discretize(y[:n], bins)

    joint = np.zeros((bins, bins))
    for i in range(n):
        xi = min(x_d[i], bins - 1)
        yi = min(y_d[i], bins - 1)
        joint[xi, yi] += 1
    joint /= n

    px = joint.sum(axis=1)
    py = joint.sum(axis=0)

    mi = 0.0
    for i in range(bins):
        for j in range(bins):
            if joint[i, j] > 0 and px[i] > 0 and py[j] > 0:
                mi += joint[i, j] * np.log2(joint[i, j] / (px[i] * py[j]))

    return float(max(mi, 0.0))


def compute_causal_link(
    source_ticker: str,
    target_ticker: str,
    source_returns: np.ndarray,
    target_returns: np.ndarray,
    max_lag: int = 5,
    significance_level: float = 0.05,
) -> CausalLink:
    """Compute full causal analysis between two time series.

    Combines transfer entropy, Granger causality, and mutual information
    into a single causal strength index.
    """
    best_te = 0.0
    best_lag = 1
    for lag in range(1, min(max_lag + 1, len(source_returns) // 3)):
        te = compute_transfer_entropy(source_returns, target_returns, lag=lag)
        if te > best_te:
            best_te = te
            best_lag = lag

    f_stat, p_value, g_lag = compute_granger_causality(
        source_returns, target_returns, max_lag,
    )

    mi = compute_mutual_information(source_returns, target_returns)

    te_norm = min(best_te / 0.3, 1.0)
    gc_norm = min(f_stat / 10.0, 1.0) if p_value < significance_level else 0.0
    mi_norm = min(mi / 0.5, 1.0)

    causal_strength = (
        te_norm * 0.40 +
        gc_norm * 0.35 +
        mi_norm * 0.25
    )

    is_sig = p_value < significance_level and best_te > 0.01

    return CausalLink(
        source=source_ticker,
        target=target_ticker,
        transfer_entropy=best_te,
        granger_f_stat=f_stat,
        granger_p_value=p_value,
        mutual_information=mi,
        causal_strength=float(causal_strength),
        is_significant=is_sig,
        lag=best_lag,
    )
