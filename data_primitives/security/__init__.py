"""Security module for AYVID — adversarial-input defenses.

Every LLM in AYVID's reasoning chain ingests text from untrusted
external sources (Reddit, StockTwits, news scrapers, SEC filings,
RSS feeds). Without sanitisation those text paths are a direct
monetary attack surface: a single homoglyph substitution in a
single headline can flip an LLM's sentiment classifier, and one day
of attack has been quantified at up to −17.7 percentage points of
annualised return (Rizvani et al., *Adversarial News and Lost
Profits*, arXiv 2601.13082v1).

This module is the single mandatory pre-LLM gate for every news-
derived string that reaches a model call.
"""

from data_primitives.security.text_sanitizer import (
    SanitizedText,
    is_safe,
    safe_for_ticker_extraction,
    sanitize_news_text,
)

__all__ = [
    "SanitizedText",
    "sanitize_news_text",
    "is_safe",
    "safe_for_ticker_extraction",
]
