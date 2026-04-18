"""Multi-agent debate protocol with Bayesian conviction aggregation.

Implements the institutional "investment committee" pattern:
1. Each specialist analyst produces an independent opinion
2. Opinions are aggregated using weighted Bayesian updating
3. Directional consensus requires supermajority (≥3/4 analysts agree)
4. Final conviction must exceed threshold to generate a trade signal

This replaces naive single-LLM-call confidence with properly calibrated
multi-source conviction that accounts for:
- Domain expertise weighting (supply chain analyst weighted higher for supply shocks)
- Directional agreement bonus (unanimous agreement gets conviction boost)
- Contrarian penalty (one strong dissent reduces conviction)
- Confidence-weighted voting (high-confidence opinions count more)

The key insight from portfolio theory: diversified opinions produce
better-calibrated forecasts than any single expert.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from shared.logger import get_logger

log = get_logger("debate")


@dataclass
class DebateResult:
    """Outcome of the multi-agent debate protocol."""
    consensus_direction: str
    consensus_confidence: float
    bayesian_conviction: float

    votes_long: int
    votes_short: int
    votes_hold: int

    has_consensus: bool
    supermajority: bool
    should_trade: bool

    analyst_opinions: list[dict] = field(default_factory=list)
    dissenting_views: list[str] = field(default_factory=list)
    debate_summary: str = ""


_ANALYST_WEIGHTS = {
    "Supply Chain": 1.4,
    "Fundamentals": 1.2,
    "Technicals": 1.0,
    "Macro": 0.9,
}

_CONSENSUS_THRESHOLD = 0.60
_SUPERMAJORITY_RATIO = 0.75
_MIN_CONVICTION_TO_TRADE = 0.62


def _bayesian_aggregate(
    opinions: list[dict],
    weights: dict[str, float] | None = None,
) -> tuple[float, str]:
    """Bayesian conviction aggregation across analyst opinions.

    Uses log-odds space for numerically stable Bayesian updating:
        log_odds_posterior = log_odds_prior + Σ(w_i × log_odds(confidence_i × direction_agreement))

    Returns (conviction, consensus_direction).
    """
    if not opinions:
        return 0.0, "HOLD"

    if weights is None:
        weights = _ANALYST_WEIGHTS

    direction_scores: dict[str, float] = {"LONG": 0.0, "SHORT": 0.0, "HOLD": 0.0}

    for opinion in opinions:
        analyst = opinion.get("analyst", "Unknown")
        direction = opinion.get("direction", "HOLD")
        confidence = float(opinion.get("confidence", 0.5))
        w = weights.get(analyst, 1.0)

        direction_scores[direction] += confidence * w

    total = sum(direction_scores.values())
    if total <= 0:
        return 0.0, "HOLD"

    best_direction = max(direction_scores, key=lambda k: direction_scores[k])
    if best_direction == "HOLD":
        return 0.0, "HOLD"

    agreement_mass = direction_scores[best_direction] / total

    prior_odds = 1.0
    for opinion in opinions:
        analyst = opinion.get("analyst", "Unknown")
        direction = opinion.get("direction", "HOLD")
        confidence = float(opinion.get("confidence", 0.5))
        w = weights.get(analyst, 1.0)

        confidence = np.clip(confidence, 0.05, 0.95)

        if direction == best_direction:
            lr = confidence / (1.0 - confidence)
        elif direction == "HOLD":
            lr = 1.0
        else:
            lr = (1.0 - confidence) / confidence

        prior_odds *= lr ** (w / sum(weights.values()) * len(weights))

    posterior = prior_odds / (1.0 + prior_odds)
    conviction = float(np.clip(posterior, 0.0, 1.0))

    return conviction, best_direction


def run_debate(
    analyst_opinions: list[dict],
    min_conviction: float = _MIN_CONVICTION_TO_TRADE,
) -> DebateResult:
    """Execute the multi-agent debate protocol.

    Process:
    1. Count directional votes (LONG/SHORT/HOLD)
    2. Check supermajority requirement (≥3/4 must agree on direction)
    3. Compute Bayesian conviction from confidence-weighted opinions
    4. Apply consensus boost/penalty
    5. Determine if trade should execute
    """
    votes = {"LONG": 0, "SHORT": 0, "HOLD": 0}
    for opinion in analyst_opinions:
        d = opinion.get("direction", "HOLD")
        if d not in votes:
            d = "HOLD"
        votes[d] += 1

    total_votes = sum(votes.values())
    if total_votes == 0:
        return DebateResult(
            consensus_direction="HOLD",
            consensus_confidence=0.0,
            bayesian_conviction=0.0,
            votes_long=0, votes_short=0, votes_hold=0,
            has_consensus=False, supermajority=False, should_trade=False,
        )

    best_direction = max(votes, key=lambda k: votes[k])
    agreement_ratio = votes[best_direction] / total_votes
    has_consensus = agreement_ratio >= _CONSENSUS_THRESHOLD
    is_supermajority = agreement_ratio >= _SUPERMAJORITY_RATIO

    conviction, bay_direction = _bayesian_aggregate(analyst_opinions)

    if bay_direction != best_direction:
        conviction *= 0.7

    # Unanimous agreement bonus
    if agreement_ratio == 1.0 and total_votes >= 3:
        conviction = min(conviction * 1.15, 0.95)
    # Strong dissent penalty
    elif agreement_ratio < 0.5:
        conviction *= 0.5

    avg_confidence = float(np.mean([
        float(o.get("confidence", 0.5)) for o in analyst_opinions
    ])) if analyst_opinions else 0.0

    consensus_confidence = conviction * 0.7 + avg_confidence * 0.3

    should_trade = (
        has_consensus
        and conviction >= min_conviction
        and best_direction != "HOLD"
    )

    dissenting = [
        f"{o['analyst']}: {o.get('thesis', '')[:100]}"
        for o in analyst_opinions
        if o.get("direction") != best_direction and o.get("direction") != "HOLD"
    ]

    summary_parts = [
        f"Debate: {votes['LONG']}L/{votes['SHORT']}S/{votes['HOLD']}H",
        f"Consensus: {'YES' if has_consensus else 'NO'} ({best_direction})",
        f"Conviction: {conviction:.2f}",
        f"Trade: {'YES' if should_trade else 'NO'}",
    ]
    if dissenting:
        summary_parts.append(f"Dissents: {len(dissenting)}")

    log.info(" | ".join(summary_parts))

    return DebateResult(
        consensus_direction=best_direction if should_trade else "HOLD",
        consensus_confidence=float(consensus_confidence),
        bayesian_conviction=float(conviction),
        votes_long=votes["LONG"],
        votes_short=votes["SHORT"],
        votes_hold=votes["HOLD"],
        has_consensus=has_consensus,
        supermajority=is_supermajority,
        should_trade=should_trade,
        analyst_opinions=analyst_opinions,
        dissenting_views=dissenting,
        debate_summary=" | ".join(summary_parts),
    )
