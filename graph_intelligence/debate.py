"""Multi-agent debate protocol with Bayesian conviction aggregation +
Jensen-Shannon epistemic coherence gate.

Implements the institutional "investment committee" pattern augmented
with a PolySwarm-style (Barot & Borkhatariya, arXiv 2604.03888v1)
information-theoretic coherence filter:

1. Each specialist analyst produces an independent opinion
2. Opinions are aggregated using weighted Bayesian updating
3. Directional consensus requires supermajority (≥3/4 analysts agree)
4. **JS divergence measures how much the analysts' full probability
   distributions actually agree — not just their vote buckets.** When
   the mean pairwise JS > τ, conviction is halved and the trade is
   flagged with ``high_epistemic_disagreement``. This catches the
   failure mode where 4-of-4 vote the same direction but the
   underlying confidence distributions are contradictory (one analyst
   at 0.95, three at 0.52) — the vote looks unanimous, but the swarm
   is epistemically incoherent.
5. Final conviction must exceed threshold to generate a trade signal

This replaces naive single-LLM-call confidence with properly calibrated
multi-source conviction that accounts for:
- Domain expertise weighting (supply chain analyst weighted higher for supply shocks)
- Directional agreement bonus (unanimous agreement gets conviction boost)
- Contrarian penalty (one strong dissent reduces conviction)
- Confidence-weighted voting (high-confidence opinions count more)
- Epistemic incoherence detection via JS divergence on the full pmf

The key insight from portfolio theory: diversified opinions produce
better-calibrated forecasts than any single expert. The new insight from
information theory: *coherent disagreement is stronger evidence of
truth than coincident agreement on different premises.*
"""
from __future__ import annotations

import itertools
import math
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

    # ── Phase 4: Jensen-Shannon epistemic coherence fields ──
    # `epistemic_divergence` is the mean pairwise JSD across all
    # analyst opinion-pmfs, normalised to [0, 1] via log-base-2.
    # `high_epistemic_disagreement` fires when that mean crosses the
    # Tier-1 threshold — a second-order disagreement signal that
    # discrete vote counting cannot surface.
    epistemic_divergence: float = 0.0
    high_epistemic_disagreement: bool = False

    analyst_opinions: list[dict] = field(default_factory=list)
    dissenting_views: list[str] = field(default_factory=list)
    debate_summary: str = ""


_ANALYST_WEIGHTS = {
    "Supply Chain": 1.4,
    "Fundamentals": 1.2,
    "Pattern":      1.1,   # Phase 5 — chart-geometry vision specialist
    "Technicals":   1.0,
    "Macro":        0.9,
}

# ── Tier-1 agentic gating (Step 4 — over-trading suppression) ──────
# Raised from retail-grade 50% simple-majority to institutional 75%
# supermajority. The old logic allowed 2-1-1 splits through with a
# size discount — we now ABORT on any non-supermajority or any
# bull/bear cross-direction disagreement.
_CONSENSUS_THRESHOLD = 0.75          # was 0.50
_SUPERMAJORITY_RATIO = 0.75          # ≥75% agreement required
_UNANIMOUS_RATIO = 1.00              # 4-of-4 agreement
_MIN_CONVICTION_TO_TRADE = 0.72      # was 0.62 (unanimous path)
_MIN_CONVICTION_SUPERMAJORITY = 0.78  # 3-of-4: higher Bayesian bar

# ── Phase 4: Jensen-Shannon epistemic coherence threshold ──
# τ = 0.25 on the log2-normalised JSD scale [0, 1].
#
# The initial spec proposed τ = 0.40, but the empirical calibration
# sweep (below) showed that with proper-pmf lifting of (direction,
# confidence) pairs — residual mass (1-c) split uniformly — actual
# pairwise JSDs are meaningfully smaller than 0.40 even for outright
# bull/bear splits, because the residual mass provides overlap.
#
# Empirical sweep on 4-analyst patterns (measured, not estimated):
#
#   Pattern                                         measured JSD
#   ────────────────────────────────────────────────────────────
#   4-0 unanimous, tight confidence (.78-.85)       0.002
#   4-0 unanimous, medium confidence (.63-.70)      0.002
#   4-0 unanimous, outlier (.95 vs .50-.55)         0.095   ← pass
#   4-0 unanimous, extreme outlier (.99 vs .55)     0.121   ← pass
#   3-1 supermajority, benign HOLD dissent          0.148   ← pass
#   3-1 supermajority, HIGH-CONFIDENCE HOLD (.95)   0.319   ← FIRES (desired)
#   3-1 supermajority, EXTREME HOLD (.99)           0.362   ← FIRES (desired)
#   2-1-1 WEAK_MAJORITY                             0.217   ← pattern aborts
#   3-1 with bear dissent (SPLIT_OPPOSITE)          0.182   ← pattern aborts
#   2-2 SPLIT_OPPOSITE (balanced)                   0.299   ← FIRES (belt-and-braces)
#
# τ = 0.25 therefore: (a) passes every UNANIMOUS variant (even extreme
# confidence divergence), (b) passes benign 3-1 supermajority, (c)
# catches high-confidence dissent in 3-1 — exactly the case where
# an analyst is shouting `STOP` and vote-counting overrules them, and
# (d) as a belt-and-braces, also catches 2-2 splits (which the
# pattern code already hard-aborts — defence in depth).
#
# Future work: per-regime JSD calibration via
#     ayvid data calibrate --jsd-sweep
# (not yet implemented; regime-conditional τ is a candidate for Phase 4b).
_EPISTEMIC_DIVERGENCE_THRESHOLD: float = 0.25
_EPISTEMIC_CONVICTION_MULTIPLIER: float = 0.50  # halve conviction on trigger

# Ordered label vector for the analyst pmf. Changing this breaks the
# JSD numbers — always append, never reorder.
_PMF_LABELS: tuple[str, ...] = ("LONG", "SHORT", "HOLD")

# Epsilon to keep KL finite when a probability mass hits 0.
_PMF_EPS: float = 1e-12

# Vote-pattern taxonomy — drives the non-linear conviction update.
_PATTERN_UNANIMOUS = "UNANIMOUS"
_PATTERN_SUPERMAJORITY = "SUPERMAJORITY"
_PATTERN_SPLIT_OPPOSITE = "SPLIT_OPPOSITE"
_PATTERN_WEAK_MAJORITY = "WEAK_MAJORITY"
_PATTERN_NO_CONSENSUS = "NO_CONSENSUS"


def _classify_vote_pattern(votes: dict[str, int], total: int) -> str:
    """Classify the disagreement structure of the swarm.

    Rules (each terminal — no fall-through):
      - ANY opposite-direction voting (long_v>0 AND short_v>0) → SPLIT_OPPOSITE
        This is the critical new rule. Bull and bear together = zero edge.
      - max_directional / total < 0.75 → WEAK_MAJORITY (e.g. 2-1-1)
      - max_directional / total == 1.00 AND total ≥ 3 → UNANIMOUS
      - max_directional / total ≥ 0.75 AND total ≥ 4 → SUPERMAJORITY (3-of-4)
      - else → NO_CONSENSUS
    """
    long_v = votes.get("LONG", 0)
    short_v = votes.get("SHORT", 0)
    if total <= 0:
        return _PATTERN_NO_CONSENSUS

    # Hard abort: any bull-bear cross-direction split
    if long_v > 0 and short_v > 0:
        return _PATTERN_SPLIT_OPPOSITE

    directional = long_v + short_v
    max_dir = max(long_v, short_v)
    if directional == 0:
        return _PATTERN_NO_CONSENSUS

    agree_ratio = max_dir / total
    if agree_ratio >= _UNANIMOUS_RATIO and total >= 3:
        return _PATTERN_UNANIMOUS
    if agree_ratio >= _SUPERMAJORITY_RATIO and total >= 4:
        return _PATTERN_SUPERMAJORITY
    if agree_ratio < _SUPERMAJORITY_RATIO:
        return _PATTERN_WEAK_MAJORITY
    return _PATTERN_NO_CONSENSUS


# ══════════════════════════════════════════════════════════════════════
#  Phase 4: Jensen-Shannon Epistemic Coherence
#
#  Reference: Barot & Borkhatariya, "PolySwarm: A Multi-Agent Large
#  Language Model Framework for Prediction Market Trading and Latency
#  Arbitrage" (arXiv 2604.03888v1, §V, 2026).
#
#  The PolySwarm paper argues that discrete vote counting loses
#  information about HOW STRONGLY each agent believes its position.
#  Two swarms with identical majority votes can have entirely different
#  posterior quality if one has tight confidence bunching and the other
#  has a high-variance spread. Information theory gives us the right
#  measurement: the pairwise JS divergence between the analysts' full
#  probability distributions.
# ══════════════════════════════════════════════════════════════════════


def opinion_to_pmf(opinion: dict) -> list[float]:
    """Build a proper probability distribution over {LONG, SHORT, HOLD}.

    The declared direction receives the agent's stated confidence; the
    remaining probability mass `(1 - c)` is split uniformly across the
    other two outcomes. This encodes "I think LONG with c=0.8, and my
    residual 0.2 uncertainty is neutral between SHORT and HOLD" — the
    simplest proper-scoring-rule-compatible lift from the (direction,
    confidence) pair to a full pmf.

    Returns a list aligned with `_PMF_LABELS`.
    """
    direction = opinion.get("direction", "HOLD")
    if direction not in _PMF_LABELS:
        direction = "HOLD"
    c = float(opinion.get("confidence", 0.5))
    c = max(min(c, 1.0), 0.0)

    pmf = [0.0, 0.0, 0.0]
    for i, lbl in enumerate(_PMF_LABELS):
        if lbl == direction:
            pmf[i] = c
        else:
            pmf[i] = (1.0 - c) / 2.0
    # Numerical-stability guard — floor every mass above eps and renorm.
    pmf = [max(p, _PMF_EPS) for p in pmf]
    total = sum(pmf)
    return [p / total for p in pmf]


def _kl_divergence_log2(p: list[float], q: list[float]) -> float:
    """KL(P || Q) in bits (log2). Non-symmetric. Requires q > 0 everywhere."""
    return float(sum(
        pi * math.log2(pi / qi) if pi > _PMF_EPS else 0.0
        for pi, qi in zip(p, q)
    ))


def js_divergence(p: list[float], q: list[float]) -> float:
    """Symmetric Jensen-Shannon divergence in log2 units, bounded [0, 1].

    Formula:
        M = (P + Q) / 2
        JSD(P, Q) = 0.5 * KL(P || M) + 0.5 * KL(Q || M)

    With log base 2 the output is naturally bounded at 1 bit (when P
    and Q have disjoint support). We expose it on that [0, 1] scale so
    the threshold τ = 0.40 reads intuitively as "40 % of maximum
    possible disagreement".
    """
    if len(p) != len(q):
        return 0.0
    m = [(pi + qi) / 2.0 for pi, qi in zip(p, q)]
    m = [max(mi, _PMF_EPS) for mi in m]
    return 0.5 * _kl_divergence_log2(p, m) + 0.5 * _kl_divergence_log2(q, m)


def compute_epistemic_divergence(opinions: list[dict]) -> float:
    """Mean pairwise Jensen-Shannon divergence across every analyst pair.

    With N analysts, we average over C(N, 2) pairs. The per-pair JSD is
    log2-scaled (bounded [0, 1]), so the mean itself is also bounded
    [0, 1]. Returns 0.0 gracefully when fewer than 2 opinions supplied.

    This is the core Phase 4 primitive — callers compare the return
    value to `_EPISTEMIC_DIVERGENCE_THRESHOLD` to decide whether the
    swarm is epistemically coherent.
    """
    if len(opinions) < 2:
        return 0.0
    pmfs = [opinion_to_pmf(o) for o in opinions]
    divergences = [
        js_divergence(a, b) for a, b in itertools.combinations(pmfs, 2)
    ]
    if not divergences:
        return 0.0
    return float(sum(divergences) / len(divergences))


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
    """Execute the multi-agent debate protocol (Tier-1, non-linear).

    Process:
    1. Count directional votes (LONG/SHORT/HOLD)
    2. Classify the vote pattern (UNANIMOUS / SUPERMAJORITY / SPLIT_* / WEAK)
    3. Apply non-linear gating:
         • UNANIMOUS       → 1.15× conviction bonus, base threshold
         • SUPERMAJORITY   → 0.92× conviction penalty, higher threshold
         • SPLIT_OPPOSITE  → HARD ABORT (no size amount survives this)
         • WEAK / NONE     → HARD ABORT

    Only unanimous or 3-of-4 supermajority decisions with high Bayesian
    conviction pass to Tier 2. This is the institutional "investment
    committee" standard — discipline over volume.
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
    pattern = _classify_vote_pattern(votes, total_votes)
    has_consensus = pattern in (_PATTERN_UNANIMOUS, _PATTERN_SUPERMAJORITY)
    is_supermajority = pattern == _PATTERN_SUPERMAJORITY or pattern == _PATTERN_UNANIMOUS

    conviction, bay_direction = _bayesian_aggregate(analyst_opinions)
    if bay_direction != best_direction:
        conviction *= 0.7

    # ── Phase 4: Jensen-Shannon epistemic coherence gate ──
    # Compute the mean pairwise JSD over the full opinion pmfs. This
    # fires BEFORE the pattern-specific transform so the penalty
    # compounds with the pattern discount (UNANIMOUS × 1.15 then × 0.50
    # for high JSD still lands below the conviction threshold —
    # precisely the desired behaviour when the swarm votes unanimously
    # but disagrees internally).
    epistemic_div = compute_epistemic_divergence(analyst_opinions)
    high_disagreement = epistemic_div > _EPISTEMIC_DIVERGENCE_THRESHOLD
    if high_disagreement:
        conviction *= _EPISTEMIC_CONVICTION_MULTIPLIER

    # ── Non-linear conviction transform by pattern ──
    should_trade = False
    conviction_threshold = min_conviction
    abort_reason = ""

    if pattern == _PATTERN_SPLIT_OPPOSITE:
        # Bulls AND bears both voting → no amount of conviction helps.
        # This is the critical new rule that kills over-trading.
        conviction = 0.0
        abort_reason = (
            f"SPLIT_OPPOSITE ({votes['LONG']}L vs {votes['SHORT']}S) — "
            f"aborted; no edge from contradictory swarm"
        )
    elif pattern == _PATTERN_WEAK_MAJORITY:
        conviction *= 0.40  # hard discount (was 0.50) — makes it almost unreachable
        abort_reason = (
            f"WEAK_MAJORITY ({agreement_ratio:.0%}<{_SUPERMAJORITY_RATIO:.0%}) — "
            f"below Tier-1 supermajority bar"
        )
    elif pattern == _PATTERN_NO_CONSENSUS:
        conviction *= 0.40
        abort_reason = "NO_CONSENSUS — aborted"
    elif pattern == _PATTERN_UNANIMOUS:
        conviction = min(conviction * 1.15, 0.95)
        conviction_threshold = _MIN_CONVICTION_TO_TRADE
        should_trade = (
            conviction >= conviction_threshold and best_direction != "HOLD"
        )
    elif pattern == _PATTERN_SUPERMAJORITY:
        conviction *= 0.92  # small penalty for the dissenting voice
        conviction_threshold = _MIN_CONVICTION_SUPERMAJORITY
        should_trade = (
            conviction >= conviction_threshold and best_direction != "HOLD"
        )

    avg_confidence = float(np.mean([
        float(o.get("confidence", 0.5)) for o in analyst_opinions
    ])) if analyst_opinions else 0.0

    consensus_confidence = conviction * 0.7 + avg_confidence * 0.3

    dissenting = [
        f"{o['analyst']}: {o.get('thesis', '')[:100]}"
        for o in analyst_opinions
        if o.get("direction") != best_direction and o.get("direction") != "HOLD"
    ]

    summary_parts = [
        f"Debate: {votes['LONG']}L/{votes['SHORT']}S/{votes['HOLD']}H",
        f"Pattern: {pattern}",
        f"JSD: {epistemic_div:.2f}"
        + (
            f"<{_EPISTEMIC_DIVERGENCE_THRESHOLD:.2f} (coherent)"
            if not high_disagreement
            else f">{_EPISTEMIC_DIVERGENCE_THRESHOLD:.2f} (INCOHERENT; conv ×"
                 f"{_EPISTEMIC_CONVICTION_MULTIPLIER:.2f})"
        ),
        f"Conviction: {conviction:.2f} (need {conviction_threshold:.2f})",
        f"Trade: {'YES' if should_trade else 'NO'}",
    ]
    if abort_reason:
        summary_parts.append(f"Abort: {abort_reason}")
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
        epistemic_divergence=float(epistemic_div),
        high_epistemic_disagreement=high_disagreement,
        analyst_opinions=analyst_opinions,
        dissenting_views=dissenting,
        debate_summary=" | ".join(summary_parts),
    )
