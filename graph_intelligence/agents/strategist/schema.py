"""Strongly-typed schemas for LLM agents in the strategy pipeline.

Every LLM output that drives downstream execution is validated through
one of these Pydantic models. Two benefits:

1. **Type safety** — the orchestrator and Kelly sizer never receive
   malformed numeric fields or hallucinated strategy names.
2. **Dynamic parameterisation** — the LLM no longer needs to match
   hardcoded thresholds (RSI < 30, z < -1.5). It emits the parameters
   itself, justified against the live regime, and the execution math
   reads them directly.

Validation policy: fields are optional with conservative defaults so a
partial LLM output degrades gracefully rather than killing the pipeline.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ── Canonical list of strategy identifiers the orchestrator can run.
# The Strategist / Supervisor emit these exact strings; anything outside
# this set is ignored (or warned on). Keep in sync with
# `strategy_orchestrator.py`.
CANONICAL_STRATEGIES: tuple[str, ...] = (
    # Index F&O
    "idx_trend_es", "idx_trend_nq", "idx_trend_ym", "idx_trend_rty",
    "idx_mr_oversold_call", "idx_mr_overbought_put",
    "idx_bull_spread", "idx_bear_spread", "idx_decline_put",
    "idx_momentum", "idx_vol_arb_call", "idx_vol_arb_put",
    "crisis_index_put", "crisis_capitulation_bounce",
    "shock_directed_call", "shock_directed_put",
    # Equity investment
    "deep_value_investment", "supply_chain_beneficiary",
    "supply_chain_oversold_dip", "trend_investment", "mr_equity_bounce",
)

MacroRegime = Literal["RISK_ON", "RISK_OFF", "TRANSITIONAL"]


class SectorRotation(BaseModel):
    favored_sectors: list[str] = Field(default_factory=list)
    unfavored_sectors: list[str] = Field(default_factory=list)


class CrossAssetSignal(BaseModel):
    signal: str = ""
    implication: str = ""


class StrategistOutput(BaseModel):
    """Typed output from the Strategist LangGraph node.

    The Strategist is now responsible for regime-driven strategy
    gating. The `forbidden_strategies` list is authoritative: the
    orchestrator MUST NOT run any strategy named here even if the
    per-instrument factor score clears local thresholds.
    """

    macro_regime: MacroRegime = "TRANSITIONAL"
    regime_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_multiplier: float = Field(default=1.0, ge=0.25, le=2.5)
    shock_context: str = ""

    sector_rotation: SectorRotation = Field(default_factory=SectorRotation)
    cross_asset_signals: list[CrossAssetSignal] = Field(default_factory=list)

    # Regime-driven strategy gating — the single most important new field.
    forbidden_strategies: list[str] = Field(default_factory=list)
    preferred_strategies: list[str] = Field(default_factory=list)

    # Free-text justification so the auditor can challenge the gating.
    gating_rationale: str = ""

    @field_validator("forbidden_strategies", "preferred_strategies")
    @classmethod
    def _filter_to_canonical(cls, v: list[str]) -> list[str]:
        return [s for s in v if s in CANONICAL_STRATEGIES]


class StrategyParameters(BaseModel):
    """LLM-chosen entry thresholds for this specific context.

    Replaces the old hardcoded `if RSI < 30` / `if z < -1.5` checks in
    every strategy branch. The Supervisor agent emits these values per
    trade — justified against realised vol, regime, and shock state —
    and the execution math reads them directly.

    All fields have sensible defaults so strategies still work when the
    LLM is offline or unreachable (`.model_dump()` gives the legacy
    static thresholds).
    """

    # Equity mean-reversion / deep-value entry
    max_rsi_entry_long: float = Field(default=30.0, ge=5.0, le=50.0)
    min_rsi_entry_short: float = Field(default=70.0, ge=50.0, le=95.0)
    max_z_entry_long: float = Field(default=-1.5, ge=-4.0, le=0.0)
    min_z_entry_short: float = Field(default=1.5, ge=0.0, le=4.0)
    max_20d_return_long: float = Field(default=-0.06, ge=-0.40, le=0.10)

    # Trend / momentum entry
    min_trend_strength_long: float = Field(default=0.15, ge=0.0, le=1.0)
    min_adx_trend: float = Field(default=22.0, ge=0.0, le=80.0)
    min_ret_5d_momentum: float = Field(default=0.008, ge=0.0, le=0.10)

    # EV gate & sizing
    min_expected_r: float = Field(default=1.5, ge=1.0, le=5.0)
    min_factor_score: float = Field(default=0.30, ge=0.0, le=1.0)
    min_conviction: float = Field(default=0.62, ge=0.50, le=0.95)

    # Volume / confirmation
    min_volume_ratio: float = Field(default=1.2, ge=0.5, le=5.0)

    rationale: str = ""

    @classmethod
    def defaults(cls) -> "StrategyParameters":
        """Construct with legacy static thresholds (backward compat)."""
        return cls()


class SupervisorDecision(BaseModel):
    """Final LLM-supervisor output merged into the trade plan.

    The Supervisor approves/rejects the trade and — crucially — emits
    the `strategy_parameters` the execution math should use. Unlike the
    old fixed-threshold world, every trade's entry conditions are now
    chosen live based on the current regime.
    """

    approved: bool = False
    direction: Literal["LONG", "SHORT", "HOLD"] = "HOLD"
    strategy_name: str = ""
    conviction: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_r: float = Field(default=0.0, ge=0.0, le=10.0)

    strategy_parameters: StrategyParameters = Field(default_factory=StrategyParameters.defaults)

    kill_reasons: list[str] = Field(default_factory=list)
    rationale: str = ""


__all__ = [
    "CANONICAL_STRATEGIES",
    "MacroRegime",
    "SectorRotation",
    "CrossAssetSignal",
    "StrategistOutput",
    "StrategyParameters",
    "SupervisorDecision",
]
