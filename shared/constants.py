"""Pure physical / mathematical constants.

These are NEVER environment-specific and NEVER operator-tunable — they are
facts about financial time, not policy choices. Operator-tunable parameters
(conviction floors, regime thresholds, EV hurdles, ingestion cadence,
screener size) live in `shared.config.Settings` and override via `.env`.

Keep this file tiny. If a value might legitimately vary across deployments,
it belongs in `Settings`, not here.
"""

from __future__ import annotations

# ── Time ──────────────────────────────────────────────────────
TRADING_DAYS_PER_YEAR: int = 252
"""Standard equity-market trading-day convention used for annualising vol,
computing Sharpe, discounting options time-to-expiry, and every other
\\sqrt{252} appearance in the codebase."""

SECONDS_PER_DAY: int = 86_400
SECONDS_PER_TRADING_DAY: int = 6 * 60 * 60 + 30 * 60  # 09:30 → 16:00 ET

# ── Rates ─────────────────────────────────────────────────────
DEFAULT_RISK_FREE_RATE: float = 0.05
"""Fallback annualised risk-free rate used by BSM pricing, Sharpe ratios,
and Kelly excess-return calculations when a live Treasury curve is
unavailable. When `openbb.fixedincome.government.treasury_rates` is
reachable, the orchestrator supersedes this value at runtime."""
