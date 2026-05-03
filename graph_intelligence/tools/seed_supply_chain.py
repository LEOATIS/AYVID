"""Curated global supply-chain seed.

Lives here (rather than `cli/commands/graph.py`, where it originally
sat) so the runner's auto-bootstrap path can import the constant
without pulling in typer/rich/click. The CLI's ``ayvid graph seed``
command continues to work — it now imports from this module too.

Each tuple is (source_ticker, target_ticker, revenue_dependency_pct,
criticality_score). ``revenue_dependency_pct`` of 0 means the edge is
informational only and gets filtered out by the seed call site. The
data is curated, not auto-discovered — production graphs grow from
here via the LLM edge-proposer pipeline.
"""

from __future__ import annotations

GLOBAL_SUPPLY_CHAIN = [
    # ── Semiconductor Fabrication (the beating heart) ─────────
    ("TSM", "AAPL", 0.25, 0.95),
    ("TSM", "NVDA", 0.18, 0.90),
    ("TSM", "AMD", 0.15, 0.85),
    ("TSM", "QCOM", 0.12, 0.80),
    ("TSM", "INTC", 0.08, 0.60),
    ("TSM", "AVGO", 0.10, 0.75),
    ("TSM", "MRVL", 0.06, 0.65),
    ("TSM", "MEDIATEK", 0.14, 0.85),
    ("005930.KS", "AAPL", 0.15, 0.70),
    ("005930.KS", "XIAOMI", 0.20, 0.80),
    ("005930.KS", "VERIZON", 0.10, 0.50),
    ("005930.KS", "T", 0.08, 0.45),
    ("UMC", "QCOM", 0.08, 0.50),
    ("GFS", "AMD", 0.10, 0.55),
    ("GFS", "QCOM", 0.12, 0.60),
    # ── Semiconductor Equipment (bottleneck layer) ────────────
    ("ASML", "TSM", 0.30, 0.99),
    ("ASML", "005930.KS", 0.20, 0.95),
    ("ASML", "INTC", 0.15, 0.90),
    ("ASML", "GFS", 0.05, 0.80),
    ("AMAT", "TSM", 0.20, 0.85),
    ("AMAT", "005930.KS", 0.15, 0.80),
    ("LRCX", "TSM", 0.18, 0.85),
    ("LRCX", "INTC", 0.12, 0.75),
    ("KLAC", "TSM", 0.15, 0.80),
    ("ENTG", "TSM", 0.10, 0.70),
    ("TER", "NVDA", 0.08, 0.60),
    # ── Consumer Electronics ──────────────────────────────────
    ("AAPL", "2317.TW", 0.60, 0.95),
    ("AAPL", "FLEX", 0.05, 0.40),
    ("DELL", "INTC", 0.25, 0.70),
    ("DELL", "AMZN", 0.08, 0.40),
    ("HPQ", "INTC", 0.20, 0.65),
    ("LNVGY", "INTC", 0.15, 0.60),
    ("LNVGY", "AMD", 0.08, 0.45),
    ("SONY", "TSM", 0.12, 0.70),
    ("SONY", "AMD", 0.15, 0.65),
    # ── Cloud / AI Infrastructure ─────────────────────────────
    ("NVDA", "MSFT", 0.20, 0.80),
    ("NVDA", "META", 0.15, 0.75),
    ("NVDA", "GOOG", 0.15, 0.75),
    ("NVDA", "AMZN", 0.12, 0.70),
    ("NVDA", "TSLA", 0.08, 0.55),
    ("NVDA", "ORCL", 0.06, 0.50),
    ("AMD", "MSFT", 0.10, 0.55),
    ("AMD", "GOOG", 0.06, 0.45),
    ("AVGO", "AAPL", 0.20, 0.80),
    ("AVGO", "GOOG", 0.08, 0.50),
    ("QCOM", "005930.KS", 0.20, 0.75),
    ("QCOM", "XIAOMI", 0.15, 0.65),
    # ── Enterprise Software (downstream) ──────────────────────
    ("MSFT", "CRM", 0.06, 0.35),
    ("MSFT", "NOW", 0.04, 0.30),
    ("AMZN", "SNOW", 0.05, 0.40),
    ("GOOG", "DDOG", 0.04, 0.30),
    ("ORCL", "SAP", 0.03, 0.25),
    # ── Automotive Supply Chain ───────────────────────────────
    ("TSLA", "PANASONIC", 0.30, 0.90),
    ("TSLA", "CATL", 0.25, 0.85),
    ("TSLA", "NXPI", 0.08, 0.60),
    ("TM", "DENSO", 0.15, 0.80),
    ("TM", "AISIN", 0.10, 0.70),
    ("BMW", "CATL", 0.12, 0.75),
    ("BMW", "CONTINENTAL", 0.10, 0.65),
    ("VW", "BOSCH", 0.15, 0.80),
    ("VW", "CONTINENTAL", 0.12, 0.70),
    ("GM", "LG_ENERGY", 0.20, 0.85),
    ("F", "SK_INNOVATION", 0.15, 0.75),
    ("RIVN", "005930.KS", 0.10, 0.60),
    # ── Energy & Commodities ──────────────────────────────────
    ("XOM", "CVX", 0.05, 0.20),
    ("SHEL", "BP", 0.04, 0.20),
    ("SHEL", "TOTALENERGIES", 0.05, 0.25),
    ("RIO", "BHP", 0.06, 0.25),
    ("RIO", "VALE", 0.08, 0.30),
    ("BHP", "CATERPILLAR", 0.10, 0.50),
    ("VALE", "TSLA", 0.05, 0.40),
    ("FREEPORT", "TSLA", 0.04, 0.35),
    ("ALBEMARLE", "TSLA", 0.08, 0.60),
    ("ALBEMARLE", "CATL", 0.12, 0.70),
    # ── Global Logistics & Shipping ───────────────────────────
    ("AMKBY", "AAPL", 0.03, 0.50),
    ("AMKBY", "005930.KS", 0.03, 0.45),
    ("AMKBY", "WALMART", 0.04, 0.55),
    ("FDX", "AMZN", 0.15, 0.60),
    ("UPS", "AMZN", 0.12, 0.55),
    ("XPO", "WALMART", 0.08, 0.45),
    ("ZIM", "COSTCO", 0.05, 0.40),
    # ── Pharmaceuticals & Biotech ─────────────────────────────
    ("ROCHE", "GENENTECH", 0.30, 0.90),
    ("NOVARTIS", "SANDOZ", 0.25, 0.85),
    ("JNJ", "JANSSEN", 0.35, 0.90),
    ("PFE", "BNTX", 0.15, 0.70),
    ("AZN", "ALEXION", 0.20, 0.75),
    ("MRK", "ORGANON", 0.10, 0.50),
    ("LLY", "AAPL", 0.00, 0.00),
    ("ABBV", "ROCHE", 0.03, 0.20),
    # ── Financials ────────────────────────────────────────────
    ("JPM", "V", 0.05, 0.30),
    ("JPM", "MA", 0.04, 0.25),
    ("GS", "MSFT", 0.03, 0.20),
    ("HSBC", "SHEL", 0.04, 0.25),
    ("HSBC", "BHP", 0.03, 0.20),
    # ── Telecom ───────────────────────────────────────────────
    ("ERICSSON", "T", 0.12, 0.60),
    ("ERICSSON", "VERIZON", 0.10, 0.55),
    ("NOKIA", "T", 0.08, 0.50),
    ("NOKIA", "DT", 0.10, 0.55),
    # ── Defense & Aerospace ───────────────────────────────────
    ("RTX", "BA", 0.15, 0.70),
    ("RTX", "LMT", 0.10, 0.60),
    ("GE", "BA", 0.20, 0.80),
    ("HON", "BA", 0.10, 0.55),
    ("HON", "AIRBUS", 0.08, 0.50),
    ("LMT", "US_DOD", 0.60, 0.95),
    ("NOC", "US_DOD", 0.55, 0.90),
    # ── Retail & Consumer ─────────────────────────────────────
    ("WALMART", "PG", 0.08, 0.40),
    ("WALMART", "NESTLE", 0.05, 0.30),
    ("AMZN", "SHOPIFY", 0.04, 0.30),
    ("COSTCO", "PG", 0.06, 0.35),
]

