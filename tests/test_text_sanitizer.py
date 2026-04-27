"""Tests for the adversarial-news sanitizer (Phase 1).

Coverage matches the attack taxonomy from Rizvani, Apruzzese, Laskov
(2026), *Adversarial News and Lost Profits* (arXiv 2601.13082v1)
plus a battery of CSS stealth variants (visibility:hidden, opacity:0,
off-screen positioning, zero-size) and negative-control benign
content that must survive the gate unaltered.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import pytest

from data_primitives.security import (
    is_safe,
    safe_for_ticker_extraction,
    sanitize_news_text,
)

if TYPE_CHECKING:
    from shared.types import NewsItem


# ────────────────────────────────────────────────────────────────────
#  Unicode homoglyph attacks  (arXiv 2601.13082v1 Fig. 1a)
# ────────────────────────────────────────────────────────────────────
class TestUnicodeHomoglyphs:
    def test_cyrillic_alphabet_is_rejected(self):
        # Paper's canonical example: Аlрhаbеt (Cyrillic А, р, а, е).
        attacked = "Аlрhаbеt shares surge after dodging antitrust breakup bullet"
        r = sanitize_news_text(attacked)
        assert r.rejected_tokens, "Cyrillic homoglyph must be rejected"
        assert "Аlрhаbеt" in r.rejected_tokens
        assert "⟨REDACTED⟩" in r.clean
        assert not is_safe(r)
        assert not safe_for_ticker_extraction(r)

    def test_cyrillic_inside_ticker(self):
        # Attacker tries to misroute AAPL → ААPL
        r = sanitize_news_text("ААPL falls 5% on earnings miss")
        assert any("ААPL" == tok for tok in r.rejected_tokens)
        assert "AAPL" not in r.clean  # NFKC does not fold Cyrillic → Latin
        assert not safe_for_ticker_extraction(r)

    def test_pure_cyrillic_uppercase_token_rejected(self):
        # Even without Latin mix, a pure-Cyrillic uppercase token at
        # ticker position is hostile under strict_ticker_guard.
        r = sanitize_news_text("АААА beats earnings", strict_ticker_guard=True)
        assert r.rejected_tokens
        r_relaxed = sanitize_news_text("АААА beats earnings", strict_ticker_guard=False)
        # When relaxed, the pure-Cyrillic token is NOT considered
        # mixed-script and passes through.
        assert not r_relaxed.rejected_tokens

    def test_greek_homoglyph_rejected(self):
        # Ι (Greek capital iota) looks identical to I (Latin I).
        r = sanitize_news_text("ΙBM beats revenue expectations")
        assert r.rejected_tokens

    def test_benign_ascii_ticker_untouched(self):
        r = sanitize_news_text("AAPL beats revenue expectations")
        assert not r.rejected_tokens
        assert r.clean == "AAPL beats revenue expectations"
        assert is_safe(r)
        assert safe_for_ticker_extraction(r)

    def test_fullwidth_ascii_normalised(self):
        # Fullwidth ASCII is NFKC-folded to regular ASCII; the attack is neutralised.
        r = sanitize_news_text("ＡＡＰＬ shares fall")
        assert "AAPL" in r.clean
        assert r.normalised_chars > 0
        assert is_safe(r)


# ────────────────────────────────────────────────────────────────────
#  Hidden-HTML injection attacks  (arXiv 2601.13082v1 Fig. 1b)
# ────────────────────────────────────────────────────────────────────
class TestHiddenHTMLInjection:
    def test_display_none_span_stripped(self):
        payload = (
            "Alphabet shares surge after dodging antitrust breakup bullet"
            '<span style="display:none"> and the company faces severe losses '
            "and layoffs</span>"
        )
        r = sanitize_news_text(payload)
        assert "losses" not in r.clean
        assert "layoffs" not in r.clean
        assert "surge" in r.clean
        assert r.dropped_html_blocks >= 1

    def test_visibility_hidden_stripped(self):
        r = sanitize_news_text(
            'Apple Q4 beat expectations<div style="visibility:hidden">'
            "Apple revenue plummets 40%</div>"
        )
        assert "plummets" not in r.clean

    def test_opacity_zero_stripped(self):
        r = sanitize_news_text(
            'TSLA record production<span style="opacity:0">TSLA fraud probe</span>'
        )
        assert "fraud" not in r.clean

    def test_off_screen_absolute_stripped(self):
        r = sanitize_news_text(
            "NVDA AI capex booms"
            '<span style="position:absolute;left:-9999px">NVDA demand collapses</span>'
        )
        assert "collapses" not in r.clean

    def test_zero_size_stripped(self):
        r = sanitize_news_text(
            'MSFT Azure growth<div style="width:0;height:0">MSFT layoffs 10%</div>'
        )
        assert "layoffs" not in r.clean

    def test_aria_hidden_stripped(self):
        r = sanitize_news_text(
            'TSLA production record<span aria-hidden="true">TSLA fraud investigation</span>'
        )
        assert "fraud" not in r.clean

    def test_hidden_attribute_stripped(self):
        r = sanitize_news_text("AAPL earnings beat<span hidden>AAPL misses guidance</span>")
        assert "misses" not in r.clean

    def test_script_tag_stripped(self):
        r = sanitize_news_text("AAPL Q3 results<script>alert(1)</script>")
        assert "<script>" not in r.clean
        assert "alert" not in r.clean

    def test_style_tag_stripped(self):
        r = sanitize_news_text("GOOG cloud growth<style>.hidden{display:none}</style>")
        assert ".hidden" not in r.clean

    def test_nested_hidden_inside_visible(self):
        # Attacker wraps a bullish headline around a hidden bearish clause
        # that itself contains a visible tag. The visible content that
        # happens to be inside a hidden subtree still must be dropped.
        r = sanitize_news_text(
            "Visible bullish headline"
            '<div style="display:none">Hidden bearish with <b>bold loss</b> text</div>'
        )
        assert "bearish" not in r.clean
        assert "bold" not in r.clean
        assert "bullish" in r.clean


# ────────────────────────────────────────────────────────────────────
#  Combined attacks
# ────────────────────────────────────────────────────────────────────
class TestCombinedAttacks:
    def test_homoglyph_and_hidden_html_together(self):
        # Homoglyph in ticker + hidden-HTML bearish injection simultaneously.
        r = sanitize_news_text(
            'Аlрhаbеt earnings beat<span style="display:none">margins collapsing rapidly</span>'
        )
        assert r.rejected_tokens, "homoglyph still rejected"
        assert r.dropped_html_blocks >= 1, "hidden span still stripped"
        assert "collapsing" not in r.clean
        assert not is_safe(r)

    def test_multiple_hidden_blocks(self):
        r = sanitize_news_text(
            'A<span style="display:none">x</span>B'
            '<div style="visibility:hidden">y</div>C'
            "<span hidden>z</span>D"
        )
        assert "x" not in r.clean
        assert "y" not in r.clean
        assert "z" not in r.clean
        assert r.dropped_html_blocks >= 3


# ────────────────────────────────────────────────────────────────────
#  Benign content — MUST survive unchanged
# ────────────────────────────────────────────────────────────────────
class TestBenignSurvival:
    @pytest.mark.parametrize(
        "text",
        [
            "Apple Inc. (AAPL) reported Q3 earnings of $24.16 billion.",
            "NVDA surged 12% after blowout earnings; AI demand remains strong.",
            "Fed hikes rates by 25 bps; SPX down 1.2%, yield curve inverts.",
            "Tesla's Q2 deliveries reached 466,140 units, beating the 445k consensus.",
            "Shares of JPMorgan Chase & Co. (JPM) gained 0.8% on upgraded guidance.",
            "OPEC+ announces 1.2mbpd cut; Brent crude up to $88.40.",
            "Berkshire Hathaway's Q1 cash pile hit $189 billion, signaling caution.",
            "Boeing (BA) 737 MAX certification delayed by FAA inquiry.",
        ],
    )
    def test_canonical_financial_headlines_untouched(self, text: str):
        r = sanitize_news_text(text)
        assert not r.rejected_tokens
        assert r.dropped_html_blocks == 0
        assert is_safe(r)
        assert safe_for_ticker_extraction(r)
        # Content should be byte-identical (no NFKC changes on plain ASCII).
        assert r.clean == text

    def test_legitimate_html_tags_strip_cleanly(self):
        # News scrapers occasionally leave in <p>, <br>, <a> — those
        # are visible and legitimate; we strip the tags but keep the text.
        r = sanitize_news_text('<p>Apple earnings beat; <a href="/aapl">details</a></p>')
        assert "Apple" in r.clean
        assert "details" in r.clean
        assert "<p>" not in r.clean
        assert "<a href" not in r.clean

    def test_empty_input(self):
        r = sanitize_news_text("")
        assert r.clean == ""
        assert is_safe(r)

    def test_none_input(self):
        r = sanitize_news_text(None)  # type: ignore
        assert r.clean == ""
        assert is_safe(r)

    def test_very_long_input_no_crash(self):
        long = "Apple earnings beat. " * 500
        r = sanitize_news_text(long)
        assert is_safe(r)
        assert "Apple" in r.clean


# ────────────────────────────────────────────────────────────────────
#  Performance budget
# ────────────────────────────────────────────────────────────────────
class TestPerformanceBudget:
    def test_under_2ms_per_headline(self):
        import time

        headline = (
            "Apple Inc. beats Q3 earnings expectations, shares jump 2.3% in "
            "after-hours trading on upgraded guidance."
        )
        iterations = 5_000
        t0 = time.perf_counter()
        for _ in range(iterations):
            sanitize_news_text(headline)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        per_call_us = elapsed_ms / iterations * 1000
        assert per_call_us < 2000.0, (
            f"sanitizer too slow: {per_call_us:.1f}µs/call "
            f"(budget 2000µs); total {elapsed_ms:.1f}ms over {iterations} iters"
        )


# ────────────────────────────────────────────────────────────────────
#  Integration with detect_live_shock — hostile-input policy
# ────────────────────────────────────────────────────────────────────
class TestShockDetectorHostilePolicy:
    def _mk(self, title: str, body: str = "") -> NewsItem:
        from shared.types import NewsItem

        return NewsItem(
            timestamp=dt.datetime(2026, 4, 20, 12, 0),
            title=title,
            source="test",
            text=body,
            symbols=["XLE"],
        )

    def test_benign_shock_still_triggers(self):
        from data_primitives.extractors.ner_pipeline import detect_live_shock

        news = [
            self._mk("Iran launches missile strikes on Israel"),
            self._mk("Strait of Hormuz blockade threatens oil supply"),
            self._mk("Sanctions package against Iran expanded"),
        ]
        intel = detect_live_shock(news)
        assert intel is not None
        assert intel["expected_direction"] == "BEARISH"
        assert intel["sanitisation"]["hostile_items_in_window"] == 0

    def test_hostile_window_refuses_shock(self):
        from data_primitives.extractors.ner_pipeline import detect_live_shock

        # 3 legitimate + 3 hostile with homoglyphs. 50% hostile → refused.
        news = [
            self._mk("Iran launches missile strikes on Israel"),
            self._mk("Strait of Hormuz blockade threatens oil"),
            self._mk("Sanctions package against Iran expanded"),
            self._mk("Irап nuclear deal stalls"),  # Cyrillic а, р
            self._mk("Аpple faces new tariff war"),  # Cyrillic А
            self._mk("Мeta layoffs sanctions fallout"),  # Cyrillic М
        ]
        intel = detect_live_shock(news)
        assert intel is None, "hostile ratio >=15% must refuse Tier-3 shock"

    def test_small_hostile_injection_still_allows_shock(self):
        from data_primitives.extractors.ner_pipeline import detect_live_shock

        # Single homoglyph in 8 items = 12.5% < 15% threshold → shock allowed.
        news = [
            self._mk("Iran launches missile strikes"),
            self._mk("Strait of Hormuz blockade"),
            self._mk("Sanctions package expanded"),
            self._mk("Oil jumps on war escalation"),
            self._mk("War risk premium widens"),
            self._mk("OPEC emergency meeting called"),
            self._mk("Tanker attacks reported"),
            self._mk("Аpple faces no impact"),  # 1 hostile, Cyrillic А
        ]
        intel = detect_live_shock(news)
        assert intel is not None, "minor hostile content should still allow shock"
        assert intel["sanitisation"]["hostile_items_in_window"] == 1
