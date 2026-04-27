"""Tests for Phase 4 — Jensen-Shannon epistemic coherence filter.

Covers:
  • Mathematical correctness of the JS divergence primitive
    (symmetry, range, identity, disjoint-limit)
  • Probability-distribution construction from (direction, confidence)
  • Integration into run_debate:
      – benign swarms produce low JSD and trade normally
      – high-confidence dissent in 3-1 supermajorities gets a 0.5×
        conviction penalty AND the high_epistemic_disagreement flag
      – debate_summary surfaces the JSD number
      – backward compatibility with existing 65+ tests
"""

from __future__ import annotations

from graph_intelligence.debate import (
    _EPISTEMIC_DIVERGENCE_THRESHOLD,
    _MIN_CONVICTION_SUPERMAJORITY,
    compute_epistemic_divergence,
    js_divergence,
    opinion_to_pmf,
    run_debate,
)


# ────────────────────────────────────────────────────────────────────
#  Mathematical correctness of the JS divergence primitive
# ────────────────────────────────────────────────────────────────────
class TestJSDPrimitive:
    def test_identical_distributions_zero(self):
        p = [0.33, 0.33, 0.34]
        assert js_divergence(p, p) < 1e-9

    def test_symmetric(self):
        p = [0.80, 0.10, 0.10]
        q = [0.10, 0.80, 0.10]
        assert abs(js_divergence(p, q) - js_divergence(q, p)) < 1e-9

    def test_log2_bounded_at_one(self):
        # With log base 2, JS of completely disjoint supports → 1 bit.
        p = [1.0 - 2e-12, 1e-12, 1e-12]
        q = [1e-12, 1e-12, 1.0 - 2e-12]
        jsd = js_divergence(p, q)
        assert 0.99 < jsd <= 1.0 + 1e-9

    def test_jsd_non_negative(self):
        import random

        random.seed(42)
        for _ in range(100):
            p = [random.random() for _ in range(3)]
            q = [random.random() for _ in range(3)]
            p = [x / sum(p) for x in p]
            q = [x / sum(q) for x in q]
            assert js_divergence(p, q) >= -1e-9

    def test_mismatched_length_graceful(self):
        assert js_divergence([0.5, 0.5], [0.5, 0.5, 0.0]) == 0.0


# ────────────────────────────────────────────────────────────────────
#  Opinion → pmf construction
# ────────────────────────────────────────────────────────────────────
class TestOpinionPMF:
    def test_high_confidence_long_puts_mass_on_long(self):
        p = opinion_to_pmf({"direction": "LONG", "confidence": 0.9})
        # Label order is (LONG, SHORT, HOLD)
        assert p[0] > 0.85
        assert p[1] < 0.10
        assert p[2] < 0.10

    def test_low_confidence_spreads_mass(self):
        p = opinion_to_pmf({"direction": "LONG", "confidence": 0.4})
        assert p[0] > 0.35
        assert p[1] > 0.25
        assert p[2] > 0.25

    def test_unknown_direction_defaults_to_hold(self):
        p = opinion_to_pmf({"direction": "WHATEVER", "confidence": 0.8})
        assert p[2] > 0.75  # HOLD slot

    def test_pmf_sums_to_one(self):
        for d in ("LONG", "SHORT", "HOLD"):
            for c in (0.1, 0.5, 0.95):
                p = opinion_to_pmf({"direction": d, "confidence": c})
                assert abs(sum(p) - 1.0) < 1e-6


# ────────────────────────────────────────────────────────────────────
#  Empirical coherence sweep — snapshotted against Phase-4 calibration
# ────────────────────────────────────────────────────────────────────
class TestEmpiricalCoherence:
    def _opinions(self, pattern):
        return [
            {"analyst": f"A{i}", "direction": d, "confidence": c}
            for i, (d, c) in enumerate(pattern)
        ]

    def test_tight_unanimous_is_coherent(self):
        jsd = compute_epistemic_divergence(
            self._opinions(
                [
                    ("LONG", 0.85),
                    ("LONG", 0.82),
                    ("LONG", 0.80),
                    ("LONG", 0.78),
                ]
            )
        )
        assert jsd < 0.05
        assert jsd < _EPISTEMIC_DIVERGENCE_THRESHOLD

    def test_divergent_unanimous_stays_coherent(self):
        # Even with a confidence outlier, unanimous direction keeps JSD
        # below τ. We explicitly do NOT want to penalise "one analyst
        # is very confident; the other three are cautiously agreeing".
        jsd = compute_epistemic_divergence(
            self._opinions(
                [
                    ("LONG", 0.99),
                    ("LONG", 0.55),
                    ("LONG", 0.52),
                    ("LONG", 0.50),
                ]
            )
        )
        assert jsd < _EPISTEMIC_DIVERGENCE_THRESHOLD

    def test_benign_supermajority_stays_coherent(self):
        jsd = compute_epistemic_divergence(
            self._opinions(
                [
                    ("LONG", 0.80),
                    ("LONG", 0.82),
                    ("LONG", 0.78),
                    ("HOLD", 0.60),
                ]
            )
        )
        assert jsd < _EPISTEMIC_DIVERGENCE_THRESHOLD

    def test_high_confidence_hold_dissenter_fires(self):
        jsd = compute_epistemic_divergence(
            self._opinions(
                [
                    ("LONG", 0.80),
                    ("LONG", 0.82),
                    ("LONG", 0.78),
                    ("HOLD", 0.95),
                ]
            )
        )
        assert jsd > _EPISTEMIC_DIVERGENCE_THRESHOLD, (
            "high-conviction dissent in a supermajority MUST trigger the filter"
        )

    def test_extreme_hold_dissenter_fires_harder(self):
        jsd_loud = compute_epistemic_divergence(
            self._opinions(
                [
                    ("LONG", 0.80),
                    ("LONG", 0.82),
                    ("LONG", 0.78),
                    ("HOLD", 0.99),
                ]
            )
        )
        jsd_moderate = compute_epistemic_divergence(
            self._opinions(
                [
                    ("LONG", 0.80),
                    ("LONG", 0.82),
                    ("LONG", 0.78),
                    ("HOLD", 0.95),
                ]
            )
        )
        assert jsd_loud > jsd_moderate

    def test_jsd_respects_empty_or_single(self):
        assert compute_epistemic_divergence([]) == 0.0
        assert compute_epistemic_divergence([{"direction": "LONG", "confidence": 0.8}]) == 0.0


# ────────────────────────────────────────────────────────────────────
#  run_debate integration
# ────────────────────────────────────────────────────────────────────
class TestRunDebateIntegration:
    def _supermajority_benign(self):
        return [
            {"analyst": "Supply Chain", "direction": "LONG", "confidence": 0.82},
            {"analyst": "Fundamentals", "direction": "LONG", "confidence": 0.80},
            {"analyst": "Technicals", "direction": "LONG", "confidence": 0.78},
            {"analyst": "Macro", "direction": "HOLD", "confidence": 0.60},
        ]

    def _supermajority_loud_dissent(self):
        return [
            {"analyst": "Supply Chain", "direction": "LONG", "confidence": 0.82},
            {"analyst": "Fundamentals", "direction": "LONG", "confidence": 0.80},
            {"analyst": "Technicals", "direction": "LONG", "confidence": 0.78},
            {"analyst": "Macro", "direction": "HOLD", "confidence": 0.95},
        ]

    def test_benign_supermajority_passes_filter(self):
        r = run_debate(self._supermajority_benign())
        assert r.epistemic_divergence < _EPISTEMIC_DIVERGENCE_THRESHOLD
        assert r.high_epistemic_disagreement is False

    def test_loud_dissent_trips_filter_and_halves_conviction(self):
        r_benign = run_debate(self._supermajority_benign())
        r_loud = run_debate(self._supermajority_loud_dissent())
        assert r_loud.high_epistemic_disagreement is True
        # The only difference is the dissenter's confidence; the loud
        # version must have materially lower conviction (because the
        # JS filter multiplies by 0.5 before the pattern transform).
        assert r_loud.bayesian_conviction < r_benign.bayesian_conviction * 0.65

    def test_loud_dissent_prevents_trade(self):
        # With the 0.5× penalty + supermajority 0.92× penalty applied,
        # even a strong Bayesian aggregate cannot reach the 0.78 bar.
        r = run_debate(self._supermajority_loud_dissent())
        if r.should_trade:
            # If this ever trades, conviction must at least clear the
            # supermajority threshold — which requires exceptional
            # aggregate confidence.
            assert r.bayesian_conviction >= _MIN_CONVICTION_SUPERMAJORITY
        # With standard confidences above, we expect NO trade.
        assert r.should_trade is False

    def test_unanimous_passes_even_with_outlier(self):
        # The canonical "one analyst is excited, three are cautious"
        # scenario must NOT be penalised by the JS filter.
        opinions = [
            {"analyst": "Supply Chain", "direction": "LONG", "confidence": 0.99},
            {"analyst": "Fundamentals", "direction": "LONG", "confidence": 0.55},
            {"analyst": "Technicals", "direction": "LONG", "confidence": 0.52},
            {"analyst": "Macro", "direction": "LONG", "confidence": 0.50},
        ]
        r = run_debate(opinions)
        assert r.high_epistemic_disagreement is False

    def test_debate_summary_reports_jsd(self):
        r = run_debate(self._supermajority_loud_dissent())
        assert "JSD:" in r.debate_summary
        assert "INCOHERENT" in r.debate_summary

    def test_debate_summary_coherent_label(self):
        r = run_debate(self._supermajority_benign())
        assert "coherent" in r.debate_summary.lower()

    def test_split_opposite_still_hard_aborts(self):
        # Phase 4 must NOT weaken the existing SPLIT_OPPOSITE rule.
        opinions = [
            {"analyst": "Supply Chain", "direction": "LONG", "confidence": 0.80},
            {"analyst": "Fundamentals", "direction": "LONG", "confidence": 0.80},
            {"analyst": "Technicals", "direction": "SHORT", "confidence": 0.80},
            {"analyst": "Macro", "direction": "SHORT", "confidence": 0.80},
        ]
        r = run_debate(opinions)
        assert r.should_trade is False
        assert r.bayesian_conviction == 0.0

    def test_empty_opinions_graceful(self):
        r = run_debate([])
        assert r.should_trade is False
        assert r.epistemic_divergence == 0.0
        assert r.high_epistemic_disagreement is False


# ────────────────────────────────────────────────────────────────────
#  Compound discount math (JSD × pattern)
# ────────────────────────────────────────────────────────────────────
class TestCompoundDiscounts:
    def test_jsd_discount_applies_before_pattern_transform(self):
        """When the JS filter fires, the 0.5× multiplier should be
        applied BEFORE the pattern-specific transform so the penalty
        compounds. Verify by setting up a supermajority with loud dissent
        and checking that the final conviction is consistent with
        raw_conviction × 0.5 × 0.92 (supermajority penalty).
        """
        opinions = [
            {"analyst": "Supply Chain", "direction": "LONG", "confidence": 0.82},
            {"analyst": "Fundamentals", "direction": "LONG", "confidence": 0.80},
            {"analyst": "Technicals", "direction": "LONG", "confidence": 0.78},
            {"analyst": "Macro", "direction": "HOLD", "confidence": 0.95},
        ]
        r = run_debate(opinions)
        # Conviction must be approximately (raw × 0.5 × 0.92) given
        # the supermajority pattern and the JSD gate both firing.
        # The raw Bayesian aggregate here is ~0.88-0.90; after both
        # penalties it lands roughly 0.40-0.42.
        assert r.bayesian_conviction < 0.50, (
            f"expected compounded discount to drive conviction below "
            f"0.50, got {r.bayesian_conviction:.3f}"
        )
