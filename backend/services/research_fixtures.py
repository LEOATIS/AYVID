"""Curated reference data for the research workspace.

HONESTY CONTRACT
────────────────
Every fundamental figure below was transcribed from the company's public
annual / quarterly report and is stored with ``provider="FIXTURE_FILING"`` and
``is_fixture=True``. Excerpts are curated *summaries* of the cited filing, not
verbatim quotes. Accession numbers are left ``None`` rather than guessed — the
UI shows "verify on EDGAR" with the company filing-index URL instead.
Upgrade path: replace with EDGAR XBRL ``companyfacts`` pulls (Phase 4).
"""

from __future__ import annotations

EDGAR_INDEX = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"

# symbol → (name, sector, industry, cik)
COMPANY_META: dict[str, tuple[str, str, str, str | None]] = {
    "NVDA": ("NVIDIA Corporation", "Technology", "Semiconductors", "1045810"),
    "AAPL": ("Apple Inc.", "Technology", "Consumer Hardware", "320193"),
    "MSFT": ("Microsoft Corporation", "Technology", "Software", "789019"),
    "TSM": ("Taiwan Semiconductor Manufacturing Co. (ADR)", "Technology", "Semiconductors", "1046179"),
    "ASML": ("ASML Holding N.V. (ADR)", "Technology", "Semiconductor Equipment", "937966"),
    "AMZN": ("Amazon.com, Inc.", "Consumer", "Internet Retail & Cloud", "1018724"),
    "META": ("Meta Platforms, Inc.", "Communication", "Internet", "1326801"),
    "GOOGL": ("Alphabet Inc. (Class A)", "Communication", "Internet", "1652044"),
    "GOOG": ("Alphabet Inc. (Class C)", "Communication", "Internet", "1652044"),
    "AMD": ("Advanced Micro Devices, Inc.", "Technology", "Semiconductors", "2488"),
    "AMAT": ("Applied Materials, Inc.", "Technology", "Semiconductor Equipment", "6951"),
    "JPM": ("JPMorgan Chase & Co.", "Financials", "Banks", "19617"),
    "INFY.NS": ("Infosys Ltd.", "Technology", "IT Services", None),
    "TCS.NS": ("Tata Consultancy Services Ltd.", "Technology", "IT Services", None),
    "HDFCBANK.NS": ("HDFC Bank Ltd.", "Financials", "Banks", None),
    "RELIANCE.NS": ("Reliance Industries Ltd.", "Energy", "Conglomerate", None),
    "AVGO": ("Broadcom Inc.", "Technology", "Semiconductors", None),
    "QCOM": ("QUALCOMM Inc.", "Technology", "Semiconductors", None),
    "INTC": ("Intel Corporation", "Technology", "Semiconductors", None),
    "MU": ("Micron Technology, Inc.", "Technology", "Semiconductors", None),
    "TXN": ("Texas Instruments Inc.", "Technology", "Semiconductors", None),
    "NXPI": ("NXP Semiconductors N.V.", "Technology", "Semiconductors", None),
    "ON": ("ON Semiconductor Corp. (onsemi)", "Technology", "Semiconductors", None),
    "MCHP": ("Microchip Technology Inc.", "Technology", "Semiconductors", None),
    "SWKS": ("Skyworks Solutions, Inc.", "Technology", "Semiconductors", None),
    "QRVO": ("Qorvo, Inc.", "Technology", "Semiconductors", None),
    "GFS": ("GlobalFoundries Inc.", "Technology", "Semiconductors", None),
    "UMC": ("United Microelectronics Corp. (ADR)", "Technology", "Semiconductors", None),
    "STM": ("STMicroelectronics N.V.", "Technology", "Semiconductors", None),
    "CRUS": ("Cirrus Logic, Inc.", "Technology", "Semiconductors", None),
    "LRCX": ("Lam Research Corp.", "Technology", "Semiconductor Equipment", None),
    "KLAC": ("KLA Corporation", "Technology", "Semiconductor Equipment", None),
    "TER": ("Teradyne, Inc.", "Technology", "Semiconductor Equipment", None),
    "ENTG": ("Entegris, Inc.", "Technology", "Semiconductor Equipment", None),
    "MKSI": ("MKS Inc.", "Technology", "Semiconductor Equipment", None),
    "ONTO": ("Onto Innovation Inc.", "Technology", "Semiconductor Equipment", None),
    "ASML.AS": ("ASML Holding N.V.", "Technology", "Semiconductor Equipment", None),
    "2330.TW": ("Taiwan Semiconductor Manufacturing Co.", "Technology", "Semiconductors", None),
    "2454.TW": ("MediaTek Inc.", "Technology", "Semiconductors", None),
    "005930.KS": ("Samsung Electronics Co.", "Technology", "Semiconductors", None),
    "000660.KS": ("SK hynix Inc.", "Technology", "Semiconductors", None),
    "2317.TW": ("Hon Hai Precision (Foxconn)", "Technology", "Electronics Manufacturing", None),
    "ADBE": ("Adobe Inc.", "Technology", "Software", None),
    "CRM": ("Salesforce, Inc.", "Technology", "Software", None),
    "NOW": ("ServiceNow, Inc.", "Technology", "Software", None),
    "ORCL": ("Oracle Corporation", "Technology", "Software", None),
    "SNOW": ("Snowflake Inc.", "Technology", "Software", None),
    "DDOG": ("Datadog, Inc.", "Technology", "Software", None),
    "CRWD": ("CrowdStrike Holdings, Inc.", "Technology", "Software", None),
    "ZS": ("Zscaler, Inc.", "Technology", "Software", None),
    "PANW": ("Palo Alto Networks, Inc.", "Technology", "Software", None),
    "NET": ("Cloudflare, Inc.", "Technology", "Software", None),
    "MDB": ("MongoDB, Inc.", "Technology", "Software", None),
    "PLTR": ("Palantir Technologies Inc.", "Technology", "Software", None),
    "IBM": ("International Business Machines", "Technology", "Software", None),
    "SAP.DE": ("SAP SE", "Technology", "Software", None),
    "DELL": ("Dell Technologies Inc.", "Technology", "Consumer Hardware", None),
    "HPQ": ("HP Inc.", "Technology", "Consumer Hardware", None),
    "CSCO": ("Cisco Systems, Inc.", "Technology", "Networking", None),
    "FLEX": ("Flex Ltd.", "Technology", "Electronics Manufacturing", None),
    "JBL": ("Jabil Inc.", "Technology", "Electronics Manufacturing", None),
    "TSLA": ("Tesla, Inc.", "Consumer", "Automotive", None),
    "NFLX": ("Netflix, Inc.", "Communication", "Internet", None),
    "BABA": ("Alibaba Group (ADR)", "Consumer", "Internet Retail", None),
    "WMT": ("Walmart Inc.", "Consumer", "Retail", None),
    "COST": ("Costco Wholesale Corp.", "Consumer", "Retail", None),
    "LLY": ("Eli Lilly and Company", "Healthcare", "Pharmaceuticals", None),
    "XOM": ("Exxon Mobil Corp.", "Energy", "Oil & Gas", None),
    "V": ("Visa Inc.", "Financials", "Payments", None),
    "MA": ("Mastercard Inc.", "Financials", "Payments", None),
}

NASDAQ = {
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AMD", "AVGO", "ASML",
    "ADBE", "AMAT", "AMGN", "BIDU", "COST", "CSCO", "CRWD", "DDOG", "GILD", "GFS", "INTC",
    "JD", "KLAC", "LRCX", "MCHP", "MDB", "MELI", "MU", "NFLX", "NXPI", "ON", "ONTO", "PANW",
    "PDD", "PYPL", "QCOM", "QRVO", "SWKS", "TER", "TXN", "ZS", "ENTG", "MKSI", "CRUS",
    "COIN", "LI", "PLTR", "HON", "QQQ", "SMH", "SOXX", "FLEX", "AAL", "UAL", "RIVN", "GRAB",
    "IBB", "TLT", "IEF",
}
ETFS = {
    "SPY", "DIA", "IWM", "GLD", "SLV", "USO", "UNG", "HYG", "LQD", "EEM", "EFA", "VEA", "VWO",
    "VGK", "INDA", "EWG", "EWJ", "EWU", "EWZ", "FXI", "KWEB", "XBI", "XLB", "XLE", "XLF",
    "XLI", "XLK", "XLP", "XLU", "XLV", "ARKK", "ARKG", "QQQ", "SMH", "SOXX", "IBB", "TLT", "IEF",
}
OTC = {"AMKBY", "IFNNY", "RNECY"}

# suffix → (country, exchange, currency)
SUFFIX_EXCHANGE: dict[str, tuple[str, str, str]] = {
    ".NS": ("IN", "NSE", "INR"),
    ".BO": ("IN", "BSE", "INR"),
    ".HK": ("HK", "HKEX", "HKD"),
    ".T": ("JP", "TSE", "JPY"),
    ".KS": ("KR", "KRX", "KRW"),
    ".TW": ("TW", "TWSE", "TWD"),
    ".L": ("GB", "LSE", "GBP"),
    ".DE": ("DE", "XETRA", "EUR"),
    ".PA": ("FR", "EPA", "EUR"),
    ".AS": ("NL", "AMS", "EUR"),
    ".SW": ("CH", "SIX", "CHF"),
    ".SA": ("BR", "B3", "BRL"),
}

# Real listings AYVID knows about but holds no local data for — they exist so
# the resolver can surface genuine ambiguity instead of silently picking one.
REFERENCE_LISTINGS: list[dict[str, str]] = [
    {
        "instrument_id": "US:NYSE:INFY",
        "symbol": "INFY",
        "name": "Infosys Ltd. (ADR)",
        "exchange": "NYSE",
        "currency": "USD",
        "country": "US",
        "sector": "Technology",
        "industry": "IT Services",
    },
]

NAME_ALIASES: dict[str, str] = {
    "nvidia": "NVDA",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "taiwan fab": "TSM",
    "asml": "ASML",
    "amazon": "AMZN",
    "meta": "META",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "applied materials": "AMAT",
    "infosys": "INFY",
    "tata consultancy": "TCS.NS",
    "jpmorgan": "JPM",
    "broadcom": "AVGO",
    "qualcomm": "QCOM",
    "intel": "INTC",
    "samsung": "005930.KS",
    "foxconn": "2317.TW",
    "hon hai": "2317.TW",
    "tesla": "TSLA",
    "onsemi": "ON",
    "on semiconductor": "ON",
    "micron": "MU",
    "lam research": "LRCX",
}

# Edges whose source is upstream (supplies the destination). All other
# curated seed edges are labelled CUSTOMER (source buys from destination).
UPSTREAM_SUPPLIERS = {
    "TSM", "ASML", "AMAT", "LRCX", "KLAC", "ENTG", "TER", "005930.KS", "UMC", "GFS",
    "NVDA", "AMD", "AVGO", "QCOM",
}

# ── Curated filing summaries ────────────────────────────────────────────
# key → document metadata. ``excerpt`` is a curated summary, never a quote.
FILINGS: dict[str, dict[str, str]] = {
    "NVDA-10K-FY2025": {
        "symbol": "NVDA",
        "title": "NVIDIA Corp. Form 10-K, fiscal year ended Jan 26, 2025",
        "doc_type": "10-K",
        "published_at": "2025-02-26",
        "locator": "Item 7 MD&A; Item 8 Consolidated Statements of Income",
        "excerpt": (
            "Revenue for fiscal 2025 was $130.5 billion, up 114% from $60.9 billion, "
            "driven by Data Center compute and networking. Gross margin was 75.0% "
            "versus 72.7% a year earlier. The company states it relies on third-party "
            "foundries, principally TSMC, to manufacture its wafers, and that sales to "
            "three direct customers were each more than 10% of total revenue."
        ),
    },
    "NVDA-10Q-Q1FY2026": {
        "symbol": "NVDA",
        "title": "NVIDIA Corp. Form 10-Q, quarter ended Apr 27, 2025",
        "doc_type": "10-Q",
        "published_at": "2025-05-28",
        "locator": "Part I Item 2 MD&A — H20 export licensing",
        "excerpt": (
            "In April 2025 the U.S. government informed NVIDIA that a licence is required "
            "to export H20 products to China. First-quarter results include a $4.5 billion "
            "charge for H20 excess inventory and purchase obligations; GAAP gross margin "
            "was 60.5% on revenue of $44.1 billion."
        ),
    },
    "NVDA-10Q-Q2FY2026": {
        "symbol": "NVDA",
        "title": "NVIDIA Corp. Form 10-Q, quarter ended Jul 27, 2025",
        "doc_type": "10-Q",
        "published_at": "2025-08-27",
        "locator": "Part I Item 1 Condensed Consolidated Statements of Income",
        "excerpt": (
            "Second-quarter revenue was $46.7 billion with GAAP gross margin of 72.4%. "
            "There were no H20 sales to China-based customers in the quarter."
        ),
    },
    "NVDA-10Q-Q3FY2026": {
        "symbol": "NVDA",
        "title": "NVIDIA Corp. Form 10-Q, quarter ended Oct 26, 2025",
        "doc_type": "10-Q",
        "published_at": "2025-11-19",
        "locator": "Part I Item 1 Condensed Consolidated Statements of Income",
        "excerpt": "Third-quarter revenue was $57.0 billion with GAAP gross margin of 73.4%.",
    },
    "AAPL-10K-FY2024": {
        "symbol": "AAPL",
        "title": "Apple Inc. Form 10-K, fiscal year ended Sep 28, 2024",
        "doc_type": "10-K",
        "published_at": "2024-11-01",
        "locator": "Item 8 Consolidated Statements of Operations",
        "excerpt": (
            "Net sales were $391.0 billion (2023: $383.3 billion); gross margin 46.2%; "
            "net income $93.7 billion. Apple states substantially all of its hardware is "
            "manufactured by outsourcing partners located primarily in Asia, and that some "
            "components, including custom chips, come from single or limited sources."
        ),
    },
    "MSFT-10K-FY2025": {
        "symbol": "MSFT",
        "title": "Microsoft Corp. Form 10-K, fiscal year ended Jun 30, 2025",
        "doc_type": "10-K",
        "published_at": "2025-07-30",
        "locator": "Item 8 Income Statements",
        "excerpt": (
            "Revenue was $281.7 billion (FY2024: $245.1 billion); operating income $128.5 "
            "billion; net income $101.8 billion."
        ),
    },
    "TSM-20F-2024": {
        "symbol": "TSM",
        "title": "Taiwan Semiconductor Manufacturing Co. Form 20-F, fiscal year 2024",
        "doc_type": "20-F",
        "published_at": "2025-04-17",
        "locator": "Item 5 Operating and Financial Review",
        "excerpt": (
            "Net revenue was NT$2,894.3 billion (2023: NT$2,161.7 billion) with gross "
            "margin of 56.1% (2023: 54.4%). The majority of wafer fabrication capacity "
            "is located in Taiwan; the filing lists cross-strait tensions, earthquakes "
            "and utility supply among key risks."
        ),
    },
    "ASML-20F-2024": {
        "symbol": "ASML",
        "title": "ASML Holding N.V. Form 20-F, fiscal year 2024",
        "doc_type": "20-F",
        "published_at": "2025-02-12",
        "locator": "Item 5 Operating and Financial Review",
        "excerpt": (
            "Total net sales were EUR 28.3 billion (2023: EUR 27.6 billion) with gross "
            "margin of 51.3%. Export-control regulations restrict shipments of certain "
            "DUV and all EUV systems to China."
        ),
    },
    "AMD-10K-FY2024": {
        "symbol": "AMD",
        "title": "Advanced Micro Devices Form 10-K, fiscal year 2024",
        "doc_type": "10-K",
        "published_at": "2025-02-05",
        "locator": "Item 8 Consolidated Statements of Operations",
        "excerpt": "Net revenue was $25.8 billion; gross margin 49%; net income $1.6 billion.",
    },
    "AMAT-10K-FY2024": {
        "symbol": "AMAT",
        "title": "Applied Materials Form 10-K, fiscal year ended Oct 27, 2024",
        "doc_type": "10-K",
        "published_at": "2024-12-13",
        "locator": "Item 8 Consolidated Statements of Operations",
        "excerpt": "Net revenue was $27.2 billion; gross margin 47.5%; net income $7.2 billion.",
    },
    "AMZN-10K-FY2024": {
        "symbol": "AMZN",
        "title": "Amazon.com Form 10-K, fiscal year 2024",
        "doc_type": "10-K",
        "published_at": "2025-02-07",
        "locator": "Item 8 Consolidated Statements of Operations",
        "excerpt": "Net sales were $638.0 billion; operating income $68.6 billion; net income $59.2 billion.",
    },
    "META-10K-FY2024": {
        "symbol": "META",
        "title": "Meta Platforms Form 10-K, fiscal year 2024",
        "doc_type": "10-K",
        "published_at": "2025-01-30",
        "locator": "Item 8 Consolidated Statements of Income",
        "excerpt": "Revenue was $164.5 billion; income from operations $69.4 billion; net income $62.4 billion.",
    },
    "GOOGL-10K-FY2024": {
        "symbol": "GOOGL",
        "title": "Alphabet Inc. Form 10-K, fiscal year 2024",
        "doc_type": "10-K",
        "published_at": "2025-02-05",
        "locator": "Item 8 Consolidated Statements of Income",
        "excerpt": "Revenues were $350.0 billion; operating income $112.4 billion; net income $100.1 billion.",
    },
    "JPM-10K-FY2024": {
        "symbol": "JPM",
        "title": "JPMorgan Chase & Co. Form 10-K, fiscal year 2024",
        "doc_type": "10-K",
        "published_at": "2025-02-14",
        "locator": "Consolidated Statements of Income",
        "excerpt": "Net income was $58.5 billion.",
    },
    "INFY-AR-FY2025": {
        "symbol": "INFY.NS",
        "title": "Infosys Ltd. Annual Report, fiscal year ended Mar 31, 2025",
        "doc_type": "EARNINGS_RELEASE",
        "published_at": "2025-04-17",
        "locator": "Consolidated statement of profit and loss (IND-AS)",
        "excerpt": "Revenue from operations was INR 162,990 crore; net profit INR 26,713 crore.",
    },
    "TCS-AR-FY2025": {
        "symbol": "TCS.NS",
        "title": "Tata Consultancy Services Annual Report, fiscal year ended Mar 31, 2025",
        "doc_type": "EARNINGS_RELEASE",
        "published_at": "2025-04-10",
        "locator": "Consolidated statement of profit and loss (IND-AS)",
        "excerpt": "Revenue from operations was INR 255,324 crore; net profit INR 48,553 crore.",
    },
}

# (filing_key, fiscal_period, period_end, metric_code, value, currency, scale, basis)
# Monetary values in millions of the stated currency; margins in percent.
FUNDAMENTALS: list[tuple[str, str, str, str, float, str, str, str]] = [
    ("NVDA-10K-FY2025", "FY2024", "2024-01-28", "REVENUE", 60922, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10K-FY2025", "FY2024", "2024-01-28", "GROSS_PROFIT", 44301, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10K-FY2025", "FY2024", "2024-01-28", "OPERATING_INCOME", 32972, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10K-FY2025", "FY2024", "2024-01-28", "NET_INCOME", 29760, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10K-FY2025", "FY2025", "2025-01-26", "REVENUE", 130497, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10K-FY2025", "FY2025", "2025-01-26", "GROSS_PROFIT", 97858, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10K-FY2025", "FY2025", "2025-01-26", "OPERATING_INCOME", 81453, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10K-FY2025", "FY2025", "2025-01-26", "NET_INCOME", 72880, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10Q-Q1FY2026", "Q1-FY2026", "2025-04-27", "REVENUE", 44062, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10Q-Q1FY2026", "Q1-FY2026", "2025-04-27", "GROSS_MARGIN_PCT", 60.5, "USD", "PERCENT", "GAAP"),
    ("NVDA-10Q-Q2FY2026", "Q2-FY2026", "2025-07-27", "REVENUE", 46743, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10Q-Q2FY2026", "Q2-FY2026", "2025-07-27", "GROSS_MARGIN_PCT", 72.4, "USD", "PERCENT", "GAAP"),
    ("NVDA-10Q-Q3FY2026", "Q3-FY2026", "2025-10-26", "REVENUE", 57006, "USD", "MILLIONS", "GAAP"),
    ("NVDA-10Q-Q3FY2026", "Q3-FY2026", "2025-10-26", "GROSS_MARGIN_PCT", 73.4, "USD", "PERCENT", "GAAP"),
    ("AAPL-10K-FY2024", "FY2023", "2023-09-30", "REVENUE", 383285, "USD", "MILLIONS", "GAAP"),
    ("AAPL-10K-FY2024", "FY2023", "2023-09-30", "GROSS_PROFIT", 169148, "USD", "MILLIONS", "GAAP"),
    ("AAPL-10K-FY2024", "FY2023", "2023-09-30", "NET_INCOME", 96995, "USD", "MILLIONS", "GAAP"),
    ("AAPL-10K-FY2024", "FY2024", "2024-09-28", "REVENUE", 391035, "USD", "MILLIONS", "GAAP"),
    ("AAPL-10K-FY2024", "FY2024", "2024-09-28", "GROSS_PROFIT", 180683, "USD", "MILLIONS", "GAAP"),
    ("AAPL-10K-FY2024", "FY2024", "2024-09-28", "OPERATING_INCOME", 123216, "USD", "MILLIONS", "GAAP"),
    ("AAPL-10K-FY2024", "FY2024", "2024-09-28", "NET_INCOME", 93736, "USD", "MILLIONS", "GAAP"),
    ("MSFT-10K-FY2025", "FY2024", "2024-06-30", "REVENUE", 245122, "USD", "MILLIONS", "GAAP"),
    ("MSFT-10K-FY2025", "FY2024", "2024-06-30", "OPERATING_INCOME", 109433, "USD", "MILLIONS", "GAAP"),
    ("MSFT-10K-FY2025", "FY2024", "2024-06-30", "NET_INCOME", 88136, "USD", "MILLIONS", "GAAP"),
    ("MSFT-10K-FY2025", "FY2025", "2025-06-30", "REVENUE", 281724, "USD", "MILLIONS", "GAAP"),
    ("MSFT-10K-FY2025", "FY2025", "2025-06-30", "OPERATING_INCOME", 128528, "USD", "MILLIONS", "GAAP"),
    ("MSFT-10K-FY2025", "FY2025", "2025-06-30", "NET_INCOME", 101832, "USD", "MILLIONS", "GAAP"),
    ("TSM-20F-2024", "FY2023", "2023-12-31", "REVENUE", 2161736, "TWD", "MILLIONS", "IFRS"),
    ("TSM-20F-2024", "FY2023", "2023-12-31", "GROSS_MARGIN_PCT", 54.4, "TWD", "PERCENT", "IFRS"),
    ("TSM-20F-2024", "FY2024", "2024-12-31", "REVENUE", 2894308, "TWD", "MILLIONS", "IFRS"),
    ("TSM-20F-2024", "FY2024", "2024-12-31", "GROSS_MARGIN_PCT", 56.1, "TWD", "PERCENT", "IFRS"),
    ("ASML-20F-2024", "FY2023", "2023-12-31", "REVENUE", 27559, "EUR", "MILLIONS", "IFRS"),
    ("ASML-20F-2024", "FY2024", "2024-12-31", "REVENUE", 28263, "EUR", "MILLIONS", "IFRS"),
    ("ASML-20F-2024", "FY2024", "2024-12-31", "GROSS_MARGIN_PCT", 51.3, "EUR", "PERCENT", "IFRS"),
    ("ASML-20F-2024", "FY2024", "2024-12-31", "NET_INCOME", 7572, "EUR", "MILLIONS", "IFRS"),
    ("AMD-10K-FY2024", "FY2024", "2024-12-28", "REVENUE", 25785, "USD", "MILLIONS", "GAAP"),
    ("AMD-10K-FY2024", "FY2024", "2024-12-28", "NET_INCOME", 1641, "USD", "MILLIONS", "GAAP"),
    ("AMD-10K-FY2024", "FY2024", "2024-12-28", "GROSS_MARGIN_PCT", 49.0, "USD", "PERCENT", "GAAP"),
    ("AMAT-10K-FY2024", "FY2024", "2024-10-27", "REVENUE", 27176, "USD", "MILLIONS", "GAAP"),
    ("AMAT-10K-FY2024", "FY2024", "2024-10-27", "GROSS_MARGIN_PCT", 47.5, "USD", "PERCENT", "GAAP"),
    ("AMAT-10K-FY2024", "FY2024", "2024-10-27", "NET_INCOME", 7177, "USD", "MILLIONS", "GAAP"),
    ("AMZN-10K-FY2024", "FY2024", "2024-12-31", "REVENUE", 637959, "USD", "MILLIONS", "GAAP"),
    ("AMZN-10K-FY2024", "FY2024", "2024-12-31", "OPERATING_INCOME", 68593, "USD", "MILLIONS", "GAAP"),
    ("AMZN-10K-FY2024", "FY2024", "2024-12-31", "NET_INCOME", 59248, "USD", "MILLIONS", "GAAP"),
    ("META-10K-FY2024", "FY2024", "2024-12-31", "REVENUE", 164501, "USD", "MILLIONS", "GAAP"),
    ("META-10K-FY2024", "FY2024", "2024-12-31", "OPERATING_INCOME", 69380, "USD", "MILLIONS", "GAAP"),
    ("META-10K-FY2024", "FY2024", "2024-12-31", "NET_INCOME", 62360, "USD", "MILLIONS", "GAAP"),
    ("GOOGL-10K-FY2024", "FY2024", "2024-12-31", "REVENUE", 350018, "USD", "MILLIONS", "GAAP"),
    ("GOOGL-10K-FY2024", "FY2024", "2024-12-31", "OPERATING_INCOME", 112390, "USD", "MILLIONS", "GAAP"),
    ("GOOGL-10K-FY2024", "FY2024", "2024-12-31", "NET_INCOME", 100118, "USD", "MILLIONS", "GAAP"),
    ("JPM-10K-FY2024", "FY2024", "2024-12-31", "NET_INCOME", 58471, "USD", "MILLIONS", "GAAP"),
    ("INFY-AR-FY2025", "FY2025", "2025-03-31", "REVENUE", 1629900, "INR", "MILLIONS", "IFRS"),
    ("INFY-AR-FY2025", "FY2025", "2025-03-31", "NET_INCOME", 267130, "INR", "MILLIONS", "IFRS"),
    ("TCS-AR-FY2025", "FY2025", "2025-03-31", "REVENUE", 2553240, "INR", "MILLIONS", "IFRS"),
    ("TCS-AR-FY2025", "FY2025", "2025-03-31", "NET_INCOME", 485530, "INR", "MILLIONS", "IFRS"),
]

METRIC_LABELS: dict[str, str] = {
    "REVENUE": "Revenue",
    "GROSS_PROFIT": "Gross profit",
    "OPERATING_INCOME": "Operating income",
    "NET_INCOME": "Net income",
    "GROSS_MARGIN_PCT": "Gross margin",
    "OPERATING_MARGIN_PCT": "Operating margin",
    "NET_MARGIN_PCT": "Net margin",
    "REVENUE_GROWTH_PCT": "Revenue growth (YoY)",
    "CLOSE_PRICE": "Close price",
    "RETURN_90D_PCT": "90-day return",
    "REALIZED_VOL_ANN": "90-day realised volatility (ann.)",
    "MAX_DRAWDOWN_PCT": "Max drawdown (series)",
    "HURST_EXPONENT": "Hurst exponent",
    "CUSTOMER_CHURN_PCT": "Customer churn",
    "CAPEX": "Capital expenditure",
}
