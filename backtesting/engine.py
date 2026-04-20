"""Institutional-grade backtesting engine.

Two-track architecture (mirrors how Citadel / Jane Street operate):

Track 1 — INDEX F&O:
    Trade index futures (ES, NQ, NIFTY, FDAX) and index options across
    all world markets. Uses proper contract multipliers, margin, and
    trailing stops with partial profit-taking.

Track 2 — EQUITY INVESTMENT:
    Long-term stock positions driven by supply chain intelligence,
    deep value, and trend following. No equity options.

Both tracks feed into a unified equity curve with proper P&L attribution.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from backtesting.cost_model import (
    COST_DISCLOSURE,
    equity_costs,
    future_costs,
    option_costs,
    spread_costs,
)
from shared.logger import get_logger
from shared.types import (
    AlphaHorizon,
    OHLCVBar,
    SignalStatus,
    TradeAction,
    TradeSignal,
)

log = get_logger("backtest")

_DATA_DIR = Path("data_store/ohlcv")
_INDEX_DIR = Path("data_store/indices")
_INTRADAY_DIR = Path("data_store/intraday")

RISK_PER_TRADE_PCT = 0.02
MAX_PORTFOLIO_RISK_PCT = 0.30

# ── Named Historical Shock Scenarios ──────────────────────────
# DEPRECATED: This static dictionary is retained only for the YC
# benchmark report (`cli.app data report --scenarios all`) and for
# regression-testing the backtester against known historical periods.
#
# The production / live path now uses `detect_live_shock()` in
# `data_primitives.extractors.ner_pipeline` which autonomously
# synthesises a `causal_intel` block from the live news stream. New
# shocks should NOT be added to this dictionary — they're discovered
# at runtime.
#
# During a walk-forward run, if `news_feed` is supplied to
# `run_backtest`, the engine will call the autonomous detector on
# every date window and override the static shock_context when a
# real-time signal is present.
SHOCK_SCENARIOS: dict[str, dict] = {
    "us_china_tariff_2018": {
        "name": "US-China Trade War Escalation",
        "start_date": "2018-06-01",
        "end_date": "2018-12-31",
        "tickers": ["AAPL", "BA", "CAT", "DE", "BABA", "JD", "FXI"],
        "indices": ["^GSPC", "^HSI"],
        "description": "Tariff escalation — industrials, tech, China exposure",
        "causal_intel": {
            "shock_type": "trade_war",
            "beneficiary_tickers": ["CAT", "DE"],
            "victim_tickers": ["BABA", "JD", "FXI", "AAPL"],
            "index_bias": {"^GSPC": "BEARISH", "^HSI": "BEARISH"},
            "expected_direction": "BEARISH",
            "causal_chain": "Tariffs → China retaliation → Apple iPhone supply hit → industrial demand fears",
        },
    },
    "powell_pivot_q4_2018": {
        "name": "Q4 2018 Powell Tightening Selloff",
        "start_date": "2018-10-01",
        "end_date": "2018-12-26",
        "tickers": ["AAPL", "MSFT", "AMZN", "NVDA", "GOOG", "META", "NFLX", "AMD", "INTC"],
        "indices": ["^GSPC", "^IXIC", "^DJI"],
        "description": "Fed hike fears + recession scare — broad tech selloff into Christmas Eve low",
        "causal_intel": {
            "shock_type": "monetary_tightening",
            "beneficiary_tickers": [],
            "victim_tickers": ["AAPL", "MSFT", "AMZN", "NVDA", "GOOG", "META", "NFLX", "AMD"],
            "index_bias": {"^GSPC": "BEARISH", "^IXIC": "BEARISH", "^DJI": "BEARISH"},
            "expected_direction": "BEARISH",
            "causal_chain": "Powell 'long way from neutral' → rate-sensitive megacap tech selloff → year-end deleveraging",
        },
    },
    "boeing_737max_2019": {
        "name": "Boeing 737 MAX Grounding",
        "start_date": "2019-03-10",
        "end_date": "2019-06-28",
        "tickers": ["BA", "AAL", "DAL", "GE"],
        "indices": ["^GSPC", "^DJI"],
        "description": "Ethiopian Airlines crash → global 737 MAX grounding",
        "causal_intel": {
            "shock_type": "regulatory_grounding",
            "beneficiary_tickers": [],
            "victim_tickers": ["BA", "GE"],
            "index_bias": {"^GSPC": "NEUTRAL", "^DJI": "BEARISH"},
            "expected_direction": "MIXED",
            "causal_chain": "Crash → FAA grounding → Boeing production halt → GE LEAP engine demand drop → airline capacity squeeze",
        },
    },
    "covid_crash_2020": {
        "name": "COVID-19 Market Crash",
        "start_date": "2020-02-15",
        "end_date": "2020-04-30",
        "tickers": ["AAPL", "MSFT", "BA", "DAL", "AAL", "AMZN", "NFLX", "NVDA", "AMGN", "JNJ"],
        "indices": ["^GSPC", "^IXIC", "^DJI", "^FTSE"],
        "description": "Pandemic crash — travel collapse, tech/pharma divergence",
        "causal_intel": {
            "shock_type": "pandemic",
            "beneficiary_tickers": ["AMZN", "NFLX", "AMGN", "JNJ"],
            "victim_tickers": ["BA", "AAL", "DAL"],
            "index_bias": {"^GSPC": "BEARISH", "^IXIC": "BEARISH", "^DJI": "BEARISH", "^FTSE": "BEARISH"},
            "expected_direction": "BEARISH",
            "causal_chain": "Pandemic → lockdowns → airline collapse → remote work surge → pharma vaccine race",
        },
    },
    "covid_recovery_2020": {
        "name": "Post-COVID Tech Megacap Rally",
        "start_date": "2020-04-15",
        "end_date": "2020-12-31",
        "tickers": ["AAPL", "MSFT", "NVDA", "AMZN", "NFLX", "GOOG", "GOOGL", "META", "AMD", "TSM", "AVGO"],
        "indices": ["^GSPC", "^IXIC"],
        "description": "Fed liquidity + WFH → tech megacap moonshot",
        "causal_intel": {
            "shock_type": "monetary_easing",
            "beneficiary_tickers": ["AAPL", "MSFT", "NVDA", "AMZN", "NFLX", "GOOG", "GOOGL", "META", "AMD", "TSM"],
            "victim_tickers": [],
            "index_bias": {"^GSPC": "BULLISH", "^IXIC": "BULLISH"},
            "expected_direction": "BULLISH",
            "causal_chain": "Fed unlimited QE + WFH demand → semiconductor supercycle → cloud capex surge",
        },
    },
    "suez_canal_2021": {
        "name": "Suez Canal Blockage",
        "start_date": "2021-03-20",
        "end_date": "2021-05-15",
        "tickers": ["FDX", "UPS", "XPO", "MATX", "COST"],
        "indices": ["^GSPC", "^STOXX50E"],
        "description": "Ever Given blocks Suez — shipping, freight, retail inventory shock",
        "causal_intel": {
            "shock_type": "supply_disruption",
            "beneficiary_tickers": ["MATX", "FDX", "UPS", "XPO"],
            "victim_tickers": ["COST"],
            "index_bias": {"^GSPC": "NEUTRAL", "^STOXX50E": "BEARISH"},
            "expected_direction": "MIXED",
            "causal_chain": "Suez blockage → shipping rates surge → air-freight overflow → retail inventory delays",
        },
    },
    "ark_meltdown_2021": {
        "name": "ARK / Hyper-Growth Selloff",
        "start_date": "2021-02-15",
        "end_date": "2021-08-31",
        "tickers": ["ARKK", "ARKG", "NFLX", "AMZN", "IBB", "TSLA", "COIN"],
        "indices": ["^IXIC", "^GSPC"],
        "description": "Rotation out of long-duration growth into value as yields rose",
        "causal_intel": {
            "shock_type": "rotation",
            "beneficiary_tickers": [],
            "victim_tickers": ["ARKK", "ARKG", "IBB", "COIN"],
            "index_bias": {"^IXIC": "BEARISH", "^GSPC": "NEUTRAL"},
            "expected_direction": "BEARISH",
            "causal_chain": "10Y yields spike → DCF compression on long-duration growth → ARK redemptions → reflexive selloff",
        },
    },
    "china_tech_crackdown_2021": {
        "name": "China Big-Tech Regulatory Crackdown",
        "start_date": "2021-07-01",
        "end_date": "2021-12-31",
        "tickers": ["BABA", "JD", "BIDU", "FXI", "KWEB", "9988.HK", "9618.HK", "0700.HK", "1299.HK"],
        "indices": ["^HSI", "000001.SS"],
        "description": "DiDi probe → tutoring crackdown → Tencent fines → Alibaba antitrust",
        "causal_intel": {
            "shock_type": "regulatory",
            "beneficiary_tickers": [],
            "victim_tickers": ["BABA", "JD", "BIDU", "FXI", "KWEB", "9988.HK", "9618.HK", "0700.HK"],
            "index_bias": {"^HSI": "BEARISH", "000001.SS": "BEARISH"},
            "expected_direction": "BEARISH",
            "causal_chain": "DiDi probe → CSRC tutoring ban → ADR delisting risk → Hang Seng tech wreck",
        },
    },
    "fed_hike_cycle_2022": {
        "name": "2022 Fed Hike Cycle / Tech Bear",
        "start_date": "2022-01-03",
        "end_date": "2022-10-14",
        "tickers": ["AAPL", "MSFT", "AMZN", "NVDA", "META", "NFLX", "GOOG", "GOOGL", "AMD", "ARKK", "TSLA"],
        "indices": ["^IXIC", "^GSPC", "^DJI"],
        "description": "Fastest hike cycle since 1980s → tech multiple compression",
        "causal_intel": {
            "shock_type": "monetary_tightening",
            "beneficiary_tickers": [],
            "victim_tickers": ["AAPL", "MSFT", "AMZN", "NVDA", "META", "NFLX", "GOOG", "AMD", "ARKK", "TSLA"],
            "index_bias": {"^IXIC": "BEARISH", "^GSPC": "BEARISH", "^DJI": "BEARISH"},
            "expected_direction": "BEARISH",
            "causal_chain": "CPI prints → Fed hawkish pivot → 75 bp hikes → DCF crush on tech megacaps",
        },
    },
    "russia_ukraine_2022": {
        "name": "Russia-Ukraine Invasion",
        "start_date": "2022-02-20",
        "end_date": "2022-05-31",
        "tickers": ["BP.L", "LMT", "GLEN.L", "BHP.L"],
        "indices": ["^GSPC", "^STOXX50E", "^FTSE"],
        "description": "Invasion → energy/commodity surge, defense bid",
        "causal_intel": {
            "shock_type": "geopolitical",
            "beneficiary_tickers": ["BP.L", "LMT", "GLEN.L", "BHP.L"],
            "victim_tickers": [],
            "index_bias": {"^GSPC": "NEUTRAL", "^STOXX50E": "BEARISH", "^FTSE": "NEUTRAL"},
            "expected_direction": "MIXED",
            "causal_chain": "Invasion → European gas cut → oil & coal surge → defense spending pivot",
        },
    },
    "semiconductor_2022": {
        "name": "Semiconductor Inventory Correction",
        "start_date": "2022-06-01",
        "end_date": "2022-12-31",
        "tickers": ["TSM", "NVDA", "AMD", "INTC", "ASML", "QCOM", "AVGO", "MU", "TXN", "LRCX", "AMAT", "MCHP", "ON", "NXPI"],
        "indices": ["^IXIC", "^TWII", "^KS11"],
        "description": "Post-COVID demand reset — DRAM/consumer chip inventory glut",
        "causal_intel": {
            "shock_type": "demand_reset",
            "beneficiary_tickers": [],
            "victim_tickers": ["NVDA", "AMD", "MU", "INTC", "AVGO", "QCOM", "MCHP", "ON", "NXPI"],
            "index_bias": {"^IXIC": "BEARISH", "^TWII": "BEARISH", "^KS11": "BEARISH"},
            "expected_direction": "BEARISH",
            "causal_chain": "PC/phone demand collapse → DRAM glut → consumer-chip inventory correction → Asia semi indices crater",
        },
    },
    "uk_mini_budget_2022": {
        "name": "UK Mini-Budget / Gilt Crisis",
        "start_date": "2022-09-15",
        "end_date": "2022-10-31",
        "tickers": ["HSBA.L", "BP.L", "AZN.L", "GSK.L", "GLEN.L", "BHP.L"],
        "indices": ["^FTSE"],
        "description": "Truss tax cuts → gilt rout → BoE intervention",
        "causal_intel": {
            "shock_type": "fiscal_shock",
            "beneficiary_tickers": ["AZN.L", "GSK.L"],
            "victim_tickers": ["HSBA.L"],
            "index_bias": {"^FTSE": "BEARISH"},
            "expected_direction": "MIXED",
            "causal_chain": "Mini-budget → gilt rout → GBP collapse → FTSE bank stress / FTSE-100 USD-earner bid",
        },
    },
    "chatgpt_ai_rally_2023": {
        "name": "ChatGPT / Generative-AI Rally",
        "start_date": "2023-01-15",
        "end_date": "2023-07-31",
        "tickers": ["NVDA", "MSFT", "GOOG", "GOOGL", "META", "AMD", "AVGO", "ASML", "TSM", "AAPL", "AMAT", "LRCX", "KLAC", "2330.TW", "005930.KS"],
        "indices": ["^IXIC", "^GSPC", "^TWII", "^KS11"],
        "description": "ChatGPT viral moment → trillion-dollar AI capex thesis",
        "causal_intel": {
            "shock_type": "ai_capex",
            "beneficiary_tickers": ["NVDA", "MSFT", "GOOG", "GOOGL", "META", "AMD", "AVGO", "ASML", "TSM", "AMAT", "LRCX", "KLAC", "2330.TW", "005930.KS"],
            "victim_tickers": [],
            "index_bias": {"^IXIC": "BULLISH", "^GSPC": "BULLISH", "^TWII": "BULLISH", "^KS11": "BULLISH"},
            "expected_direction": "BULLISH",
            "causal_chain": "ChatGPT → hyperscaler GPU bid → NVDA Q1 guide → semicap supercycle → Asian fab beneficiaries",
        },
    },
    "svb_collapse_2023": {
        "name": "SVB / Regional Bank Crisis",
        "start_date": "2023-03-01",
        "end_date": "2023-04-30",
        "tickers": ["JPM", "ITUB4.SA", "HSBA.L", "ABN.AS", "INGA.AS"],
        "indices": ["^GSPC", "^STOXX50E", "^FTSE"],
        "description": "SVB run → Signature/First Republic → Credit Suisse → flight to quality",
        "causal_intel": {
            "shock_type": "banking_crisis",
            "beneficiary_tickers": ["JPM"],
            "victim_tickers": ["HSBA.L", "ABN.AS", "INGA.AS"],
            "index_bias": {"^GSPC": "NEUTRAL", "^STOXX50E": "BEARISH", "^FTSE": "BEARISH"},
            "expected_direction": "MIXED",
            "causal_chain": "SVB run → regional bank deposit flight → JPM beneficiary bid → European bank contagion",
        },
    },
    "hamas_israel_2023": {
        "name": "Hamas-Israel War",
        "start_date": "2023-10-07",
        "end_date": "2023-12-31",
        "tickers": ["BP.L", "LMT", "AZN.L"],
        "indices": ["^GSPC", "^STOXX50E"],
        "description": "Oct 7 attack → defense bid, oil tail risk, regional escalation fear",
        "causal_intel": {
            "shock_type": "geopolitical",
            "beneficiary_tickers": ["BP.L", "LMT"],
            "victim_tickers": [],
            "index_bias": {"^GSPC": "NEUTRAL", "^STOXX50E": "BEARISH"},
            "expected_direction": "MIXED",
            "causal_chain": "Oct 7 attack → defense order pipeline → oil risk-premium → Europe energy import stress",
        },
    },
    "red_sea_2023": {
        "name": "Red Sea / Houthi Shipping Attacks",
        "start_date": "2023-11-15",
        "end_date": "2024-03-31",
        "tickers": ["ZIM", "MATX", "FDX", "UPS", "XPO", "BP.L"],
        "indices": ["^GSPC", "^STOXX50E"],
        "description": "Houthi attacks → Cape rerouting → freight rate spike",
        "causal_intel": {
            "shock_type": "supply_disruption",
            "beneficiary_tickers": ["ZIM", "MATX", "BP.L"],
            "victim_tickers": [],
            "index_bias": {"^GSPC": "NEUTRAL", "^STOXX50E": "BEARISH"},
            "expected_direction": "MIXED",
            "causal_chain": "Houthi attacks → Suez avoidance → 14-day longer Cape route → container freight spike → tanker bid",
        },
    },
    "nvidia_blowout_2024": {
        "name": "NVIDIA Q4 Blowout / AI Capex Phase 2",
        "start_date": "2024-02-01",
        "end_date": "2024-06-28",
        "tickers": ["NVDA", "AMD", "MU", "AVGO", "ASML", "TSM", "AMAT", "LRCX", "MSFT", "GOOG", "GOOGL", "META", "2330.TW", "005930.KS", "000660.KS"],
        "indices": ["^IXIC", "^GSPC", "^TWII", "^KS11"],
        "description": "NVDA Q4 FY24 print → datacentre revenue +400% YoY → second AI leg",
        "causal_intel": {
            "shock_type": "ai_capex",
            "beneficiary_tickers": ["NVDA", "AMD", "MU", "AVGO", "ASML", "TSM", "AMAT", "LRCX", "MSFT", "GOOG", "META", "2330.TW", "005930.KS", "000660.KS"],
            "victim_tickers": [],
            "index_bias": {"^IXIC": "BULLISH", "^GSPC": "BULLISH", "^TWII": "BULLISH", "^KS11": "BULLISH"},
            "expected_direction": "BULLISH",
            "causal_chain": "NVDA Q4 print → hyperscaler reaffirm → HBM (SK Hynix) bid → TSMC CoWoS bottleneck → Asia semi melt-up",
        },
    },
    "yen_carry_unwind_2024": {
        "name": "Yen Carry Trade Unwind",
        "start_date": "2024-07-15",
        "end_date": "2024-09-15",
        "tickers": ["7203.T", "6758.T", "6861.T", "9984.T", "9433.T", "NVDA", "AAPL"],
        "indices": ["^N225", "^IXIC", "^GSPC"],
        "description": "BoJ Aug-1 hike → JPY surge → carry-trade margin liquidation → Aug-5 Black Monday",
        "causal_intel": {
            "shock_type": "fx_unwind",
            "beneficiary_tickers": [],
            "victim_tickers": ["7203.T", "6758.T", "6861.T", "9984.T", "9433.T", "NVDA", "AAPL"],
            "index_bias": {"^N225": "BEARISH", "^IXIC": "BEARISH", "^GSPC": "BEARISH"},
            "expected_direction": "BEARISH",
            "causal_chain": "BoJ hike → JPY surge → carry-trade liquidation → Topix -12% in 3 days → US tech contagion",
        },
    },
    "trump_election_rally_2024": {
        "name": "Trump 2.0 Election Rally",
        "start_date": "2024-11-01",
        "end_date": "2024-12-31",
        "tickers": ["JPM", "CAT", "DE", "GE", "BA", "TSLA", "COIN"],
        "indices": ["^GSPC", "^DJI", "^RUT"],
        "description": "Nov-5 win → deregulation/tax-cut bid → small-cap & domestic industrial rally",
        "causal_intel": {
            "shock_type": "political",
            "beneficiary_tickers": ["JPM", "CAT", "DE", "GE", "TSLA", "COIN"],
            "victim_tickers": [],
            "index_bias": {"^GSPC": "BULLISH", "^DJI": "BULLISH", "^RUT": "BULLISH"},
            "expected_direction": "BULLISH",
            "causal_chain": "Trump win → deregulation/tax-cut narrative → bank dereg → infra spend → Tesla/COIN crypto bid → small-cap reflation",
        },
    },
    "deepseek_jan_2025": {
        "name": "DeepSeek R1 / China AI Shock",
        "start_date": "2025-01-20",
        "end_date": "2025-03-31",
        "tickers": ["NVDA", "AMD", "ASML", "AVGO", "BABA", "JD", "BIDU", "MSFT", "GOOG", "META"],
        "indices": ["^IXIC", "^HSI", "^GSPC"],
        "description": "DeepSeek R1 release → AI capex re-rating fears → China tech bid",
        "causal_intel": {
            "shock_type": "ai_capex_doubt",
            "beneficiary_tickers": ["BABA", "JD", "BIDU"],
            "victim_tickers": ["NVDA", "AMD", "ASML", "AVGO"],
            "index_bias": {"^IXIC": "BEARISH", "^HSI": "BULLISH", "^GSPC": "BEARISH"},
            "expected_direction": "MIXED",
            "causal_chain": "DeepSeek R1 efficiency claim → US AI capex re-rating fear → NVDA -17% Jan-27 → China AI ecosystem bid",
        },
    },
}


async def run_scenario_backtest(
    scenario_name: str,
    initial_equity: float = 1_000_000.0,
    **kwargs,
) -> "BacktestResult":
    """Run a backtest targeting a named historical shock scenario."""
    if scenario_name not in SHOCK_SCENARIOS:
        available = ", ".join(SHOCK_SCENARIOS.keys())
        raise ValueError(f"Unknown scenario '{scenario_name}'. Available: {available}")

    scenario = SHOCK_SCENARIOS[scenario_name]
    combined_tickers = list(set(scenario["tickers"]))
    indices_for_scenario = scenario.get("indices", [])

    log.info(
        f"Running scenario backtest: {scenario['name']} "
        f"({scenario['start_date']} → {scenario['end_date']})"
    )

    causal = scenario.get("causal_intel", {})
    shock_ctx = {
        "supply_chain_shock": True,
        "shock_source": scenario["name"],
        "shock_tickers": set(scenario["tickers"]),
        "shock_magnitude": -0.08,
        "causal_intel": causal,
        "beneficiary_tickers": set(causal.get("beneficiary_tickers", [])),
        "victim_tickers": set(causal.get("victim_tickers", [])),
        "index_bias": causal.get("index_bias", {}),
    }

    from graph_intelligence.agents.strategy_orchestrator import TRADEABLE_INDEX_SYMBOLS
    original_indices = list(TRADEABLE_INDEX_SYMBOLS)
    try:
        TRADEABLE_INDEX_SYMBOLS.clear()
        TRADEABLE_INDEX_SYMBOLS.extend(indices_for_scenario)

        result = await run_backtest(
            tickers=combined_tickers,
            initial_equity=initial_equity,
            start_date=scenario["start_date"],
            end_date=scenario["end_date"],
            shock_context=shock_ctx,
            **kwargs,
        )
    finally:
        TRADEABLE_INDEX_SYMBOLS.clear()
        TRADEABLE_INDEX_SYMBOLS.extend(original_indices)

    result.metadata = result.metadata or {}
    result.metadata["scenario"] = scenario_name
    result.metadata["scenario_name"] = scenario["name"]
    result.metadata["scenario_description"] = scenario["description"]
    return result


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> dt.date:
    """Return the n-th occurrence of a weekday (0=Mon..6=Sun) in a month."""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    """Return the last occurrence of a weekday in a month."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    d = dt.date(year, month, last_day)
    while d.weekday() != weekday:
        d -= dt.timedelta(days=1)
    return d


def _next_expiry(trade_date: dt.date, rule: str) -> dt.date:
    """Find the next expiry date on or after trade_date for a given rule."""
    y, m = trade_date.year, trade_date.month

    for month_offset in range(4):
        cm = m + month_offset
        cy = y + (cm - 1) // 12
        cm = ((cm - 1) % 12) + 1

        if rule == "3rd_fri":
            exp = _nth_weekday_of_month(cy, cm, 4, 3)  # Fri=4
        elif rule == "3rd_thu":
            exp = _nth_weekday_of_month(cy, cm, 3, 3)
        elif rule == "3rd_wed":
            exp = _nth_weekday_of_month(cy, cm, 2, 3)
        elif rule == "last_thu":
            exp = _last_weekday_of_month(cy, cm, 3)
        elif rule == "2nd_fri":
            exp = _nth_weekday_of_month(cy, cm, 4, 2)
        elif rule == "2nd_thu":
            exp = _nth_weekday_of_month(cy, cm, 3, 2)
        elif rule == "2nd_last_bd":
            import calendar
            last_day = calendar.monthrange(cy, cm)[1]
            d = dt.date(cy, cm, last_day)
            bd_count = 0
            while bd_count < 2:
                if d.weekday() < 5:
                    bd_count += 1
                if bd_count < 2:
                    d -= dt.timedelta(days=1)
            exp = d
        elif rule == "wed_near_15th":
            d = dt.date(cy, cm, 15)
            while d.weekday() != 2:  # Wed
                d += dt.timedelta(days=1)
            exp = d
        else:
            exp = _nth_weekday_of_month(cy, cm, 4, 3)

        if exp >= trade_date:
            return exp
    return trade_date + dt.timedelta(days=30)


def _next_weekly_expiry(trade_date: dt.date, rule: str) -> dt.date:
    """Find the nearest weekly option expiry on or after trade_date."""
    if rule == "weekly_thu":
        target_wd = 3
    elif rule == "weekly_fri":
        target_wd = 4
    else:
        return _next_expiry(trade_date, rule.replace("weekly_", "3rd_"))

    d = trade_date
    while d.weekday() != target_wd:
        d += dt.timedelta(days=1)
    if d == trade_date:
        d += dt.timedelta(days=7)
    return d


def _compute_lots_from_risk(
    available_capital: float,
    stop_loss_pts: float,
    multiplier: int,
    usd_rate: float,
    risk_pct: float = RISK_PER_TRADE_PCT,
) -> int:
    """Institutional lot sizing: risk 2% of available capital per trade.

    lots = risk_amount / (stop_loss_distance_pts * multiplier * usd_rate)
    """
    risk_amount = available_capital * risk_pct
    risk_per_lot = stop_loss_pts * multiplier * usd_rate
    if risk_per_lot <= 0:
        return 1
    return max(1, int(risk_amount / risk_per_lot))


@dataclass
class BacktestTrade:
    signal: TradeSignal
    entry_price: float
    entry_date: dt.datetime
    exit_price: float | None = None
    exit_date: dt.datetime | None = None
    shares: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None


@dataclass
class BacktestResult:
    start_date: dt.datetime
    end_date: dt.datetime
    initial_equity: float
    final_equity: float
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_hold_days: float = 0.0
    profit_factor: float = 0.0
    trades: list[BacktestTrade] = field(default_factory=list)
    fno_trade_log: list[Any] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    shocks_analyzed: int = 0
    signals_generated: int = 0
    signals_executed: int = 0
    signals_filtered: int = 0
    extra_stats: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "period": f"{self.start_date.date()} → {self.end_date.date()}",
            "initial_equity": self.initial_equity,
            "final_equity": round(self.final_equity, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "annualized_return_pct": round(self.annualized_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 2),
            "avg_hold_days": round(self.avg_hold_days, 1),
            "profit_factor": round(self.profit_factor, 2),
            "shocks_analyzed": self.shocks_analyzed,
            "signals_generated": self.signals_generated,
            "signals_filtered": self.signals_filtered,
        }
        if self.extra_stats:
            d["extra_stats"] = self.extra_stats
        return d


def _load_local_bars(symbol: str) -> list[OHLCVBar]:
    path = _DATA_DIR / f"{symbol}.jsonl"
    if not path.exists():
        return []
    bars: list[OHLCVBar] = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            try:
                bars.append(OHLCVBar.model_validate_json(line))
            except Exception:
                continue
    bars.sort(key=lambda b: b.timestamp)
    return bars


def _load_index_bars(symbol: str) -> list[OHLCVBar]:
    """Load index OHLCV bars from data_store/indices/."""
    safe_name = symbol.replace("^", "").replace(".", "_")
    path = _INDEX_DIR / f"{safe_name}.jsonl"
    if not path.exists():
        return []
    bars: list[OHLCVBar] = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            try:
                bars.append(OHLCVBar.model_validate_json(line))
            except Exception:
                continue
    bars.sort(key=lambda b: b.timestamp)
    return bars


def _save_index_bars(symbol: str, bars: list[OHLCVBar]) -> None:
    """Persist index bars to data_store/indices/."""
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = symbol.replace("^", "").replace(".", "_")
    path = _INDEX_DIR / f"{safe_name}.jsonl"
    with open(path, "w") as f:
        for bar in bars:
            f.write(bar.model_dump_json() + "\n")


def _save_equity_bars(symbol: str, bars: list[OHLCVBar]) -> None:
    """Persist equity bars to data_store/ohlcv/."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _DATA_DIR / f"{symbol}.jsonl"
    with open(path, "w") as f:
        for bar in bars:
            f.write(bar.model_dump_json() + "\n")


# ── Data Refresh & Smart Screening ───────────────────────────

async def refresh_all_data(days: int = 120) -> dict[str, Any]:
    """Refresh ALL data before backtesting — the AI-native pipeline.

    1. Fetch fresh OHLCV for all world indices
    2. Screen & rank the best equities dynamically
    3. Fetch fresh OHLCV for top-ranked equities
    4. Return stats on what was refreshed

    This ensures every backtest uses the freshest data and the best
    dynamically-selected stocks — not a stale hardcoded list.
    """
    from data_primitives.extractors.openbb_client import fetch_ohlcv
    from graph_intelligence.agents.strategy_orchestrator import TRADEABLE_INDEX_SYMBOLS

    stats: dict[str, Any] = {"indices_refreshed": 0, "equities_refreshed": 0, "errors": []}

    # ── Step 1: Refresh all world indices ─────────────────────────
    log.info("Refreshing world index data...")
    for idx_sym in TRADEABLE_INDEX_SYMBOLS:
        try:
            bars = await fetch_ohlcv(idx_sym, days=days)
            if bars and len(bars) >= 20:
                _save_index_bars(idx_sym, bars)
                stats["indices_refreshed"] += 1
        except Exception as exc:
            log.debug(f"Index refresh skip {idx_sym}: {exc}")

    log.info(f"Refreshed {stats['indices_refreshed']}/{len(TRADEABLE_INDEX_SYMBOLS)} indices")

    # ── Step 2: Screen best equities dynamically ──────────────────
    log.info("Screening best equities from global universe...")
    top_tickers = await screen_best_equities(days=days)
    stats["equities_screened"] = len(top_tickers)

    # ── Step 3: Fetch fresh OHLCV for top equities ────────────────
    log.info(f"Fetching OHLCV for {len(top_tickers)} screened equities...")
    for ticker in top_tickers:
        try:
            bars = await fetch_ohlcv(ticker, days=days)
            if bars and len(bars) >= 20:
                _save_equity_bars(ticker, bars)
                stats["equities_refreshed"] += 1
        except Exception as exc:
            log.debug(f"Equity refresh skip {ticker}: {exc}")

    log.info(
        f"Data refresh complete: {stats['indices_refreshed']} indices, "
        f"{stats['equities_refreshed']} equities"
    )
    return stats


async def screen_best_equities(
    days: int = 120,
    top_n: int = 50,
) -> list[str]:
    """AI-native equity screener: rank the global universe by multi-factor score.

    Factors (equal-weighted composite):
    - Momentum: 20d return + 5d return (trend confirmation)
    - Value: 20d drawdown from peak (oversold = opportunity)
    - Quality: Hurst exponent > 0.5 (trending, not random walk)
    - Volume: relative volume spike (institutional interest)
    - Volatility: realized vol (higher vol = more alpha opportunity)

    Returns the top_n tickers sorted by composite score.
    """
    from data_primitives.extractors.universe_builder import GLOBAL_EQUITY_SCREENS
    from data_primitives.extractors.openbb_client import fetch_ohlcv

    all_tickers: list[str] = []
    for region, tickers in GLOBAL_EQUITY_SCREENS.items():
        all_tickers.extend(tickers)
    all_tickers = sorted(set(all_tickers))

    # First check local data, then fetch missing
    ticker_scores: list[tuple[str, float]] = []

    for ticker in all_tickers:
        bars = _load_local_bars(ticker)
        if not bars or len(bars) < 30:
            continue

        closes = np.array([b.close for b in bars])
        volumes = np.array([b.volume for b in bars], dtype=float)

        if len(closes) < 30:
            continue

        # Momentum factor: 20d + 5d returns
        ret_20d = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else 0.0
        ret_5d = closes[-1] / closes[-6] - 1 if len(closes) >= 6 else 0.0
        momentum = ret_20d * 0.6 + ret_5d * 0.4

        # Value factor: how far below 20d high (oversold depth)
        high_20d = float(np.max(closes[-20:]))
        drawdown = (closes[-1] / high_20d - 1) if high_20d > 0 else 0.0
        value = -drawdown  # deeper drawdown = more value opportunity

        # Quality: Hurst exponent (trending > 0.5)
        if len(closes) >= 20:
            log_ret = np.diff(np.log(closes[-21:]))
            hurst = _compute_hurst_fast(closes[-30:])
        else:
            hurst = 0.5

        # Volume factor: recent vs average
        avg_vol = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else 1.0
        recent_vol = float(np.mean(volumes[-3:])) if len(volumes) >= 3 else avg_vol
        vol_ratio = recent_vol / max(avg_vol, 1) if avg_vol > 0 else 1.0

        # Realized vol (higher = more opportunity for active trading)
        if len(closes) >= 21:
            log_returns = np.diff(np.log(closes[-21:]))
            rv = float(np.std(log_returns, ddof=1) * np.sqrt(252))
        else:
            rv = 0.20

        # Composite score (each factor normalized to ~0-1 range)
        score = (
            abs(momentum) * 2.0          # strong movers either direction
            + value * 1.5                 # oversold = value
            + max(hurst - 0.45, 0) * 3.0 # trending stocks
            + max(vol_ratio - 1.0, 0) * 1.0  # volume spikes
            + min(rv, 0.80) * 0.5        # volatile enough for alpha
        )
        ticker_scores.append((ticker, score))

    # Also try to fetch and score tickers we don't have locally
    locally_missing: list[str] = [t for t in all_tickers if not _load_local_bars(t)]
    if locally_missing:
        log.info(f"Fetching {min(len(locally_missing), 30)} missing equities for screening...")
        for ticker in locally_missing[:30]:
            try:
                bars = await fetch_ohlcv(ticker, days=days)
                if bars and len(bars) >= 30:
                    _save_equity_bars(ticker, bars)
                    closes = np.array([b.close for b in bars])
                    ret_20d = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else 0.0
                    score = abs(ret_20d) * 2.0
                    ticker_scores.append((ticker, score))
            except Exception:
                pass

    ticker_scores.sort(key=lambda x: x[1], reverse=True)
    top = [t for t, _ in ticker_scores[:top_n]]
    log.info(f"Screened {len(ticker_scores)} equities, selected top {len(top)}")
    return top


def _compute_metrics(
    trades: list[BacktestTrade],
    equity_curve: list[float],
    initial_equity: float,
    days: int,
) -> dict[str, float]:
    if not trades:
        return {
            "total_return_pct": 0, "annualized_return_pct": 0,
            "sharpe_ratio": 0, "max_drawdown_pct": 0,
            "win_rate": 0, "avg_win_pct": 0, "avg_loss_pct": 0,
            "avg_hold_days": 0, "profit_factor": 0,
        }

    closed = [t for t in trades if t.is_closed]
    winners = [t for t in closed if t.pnl > 0]
    losers = [t for t in closed if t.pnl <= 0]

    final_eq = equity_curve[-1] if equity_curve else initial_equity
    total_return = (final_eq / initial_equity - 1) * 100
    years = max(days / 365.25, 0.01)
    ann_return = ((final_eq / initial_equity) ** (1 / years) - 1) * 100

    if len(equity_curve) > 1:
        daily_returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
        sharpe = (np.mean(daily_returns) / max(np.std(daily_returns), 1e-10)) * np.sqrt(252)
    else:
        sharpe = 0.0

    peak = initial_equity
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100
        max_dd = max(max_dd, dd)

    gross_win = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))
    profit_factor = gross_win / max(gross_loss, 1e-10)

    return {
        "total_return_pct": total_return,
        "annualized_return_pct": ann_return,
        "sharpe_ratio": float(sharpe),
        "max_drawdown_pct": max_dd,
        "win_rate": len(winners) / max(len(closed), 1) * 100,
        "avg_win_pct": float(np.mean([t.pnl_pct for t in winners])) if winners else 0,
        "avg_loss_pct": float(np.mean([t.pnl_pct for t in losers])) if losers else 0,
        "avg_hold_days": float(np.mean([t.hold_days for t in closed])) if closed else 0,
        "profit_factor": profit_factor,
    }


def _bollinger_signal(
    closes: np.ndarray,
    volumes: np.ndarray,
    period: int = 20,
    entry_sigma: float = 2.0,
    stop_sigma: float = 3.0,
) -> dict | None:
    """Bollinger Band Bounce + Confirmation strategy.

    Enters when price bounces off the 2σ band with next-day confirmation.
    TP is at the 20-day mean (guaranteed reversion target for mean-reverting assets).
    SL is at 3σ (extreme protection, wide enough to survive noise).
    """
    if len(closes) < period + 2:
        return None

    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:], ddof=1)
    if std < 1e-10:
        return None

    upper_2s = sma + entry_sigma * std
    lower_2s = sma - entry_sigma * std
    upper_3s = sma + stop_sigma * std
    lower_3s = sma - stop_sigma * std

    current = closes[-1]
    prev = closes[-2]
    prev2 = closes[-3] if len(closes) > period + 1 else prev

    z_score = (current - sma) / std
    avg_vol = np.mean(volumes[-(period + 1):-1]) if len(volumes) > period else np.mean(volumes)
    vol_ratio = volumes[-1] / max(avg_vol, 1) if avg_vol > 0 else 1.0

    # --- LONG signal: price was below lower band, now bouncing ---
    if prev <= lower_2s and current > prev:
        bounce_pct = (current - prev) / prev * 100
        if bounce_pct > 0.1:
            tp_price = sma
            sl_price = lower_3s
            rr = (tp_price - current) / max(current - sl_price, 0.01)
            return {
                "direction": "LONG",
                "entry": current,
                "tp": tp_price,
                "sl": sl_price,
                "z_score": z_score,
                "rr_ratio": rr,
                "vol_ratio": vol_ratio,
                "bounce_pct": bounce_pct,
                "rationale": f"BB bounce: z={z_score:+.1f} bounce={bounce_pct:.1f}% vol_r={vol_ratio:.1f}",
            }

    # --- SHORT signal: price was above upper band, now retreating ---
    if prev >= upper_2s and current < prev:
        retreat_pct = (prev - current) / prev * 100
        if retreat_pct > 0.1:
            tp_price = sma
            sl_price = upper_3s
            rr = (current - tp_price) / max(sl_price - current, 0.01)
            return {
                "direction": "SHORT",
                "entry": current,
                "tp": tp_price,
                "sl": sl_price,
                "z_score": z_score,
                "rr_ratio": rr,
                "vol_ratio": vol_ratio,
                "bounce_pct": retreat_pct,
                "rationale": f"BB retreat: z={z_score:+.1f} ret={retreat_pct:.1f}% vol_r={vol_ratio:.1f}",
            }

    return None


def _divergence_signal(
    ticker: str,
    closes: np.ndarray,
    all_closes: dict[str, np.ndarray],
    period: int = 20,
) -> dict | None:
    """Cross-asset divergence detection.

    If a ticker's return deviates significantly from the sector average,
    fade the divergence back to equilibrium.
    """
    if len(closes) < period + 1:
        return None

    ticker_return = (closes[-1] / closes[-period] - 1)

    peer_returns: list[float] = []
    for other_ticker, other_closes in all_closes.items():
        if other_ticker == ticker:
            continue
        if len(other_closes) >= period + 1:
            pr = (other_closes[-1] / other_closes[-period] - 1)
            peer_returns.append(pr)

    if len(peer_returns) < 5:
        return None

    avg_peer = float(np.mean(peer_returns))
    std_peer = float(np.std(peer_returns))
    if std_peer < 1e-10:
        return None

    divergence = (ticker_return - avg_peer) / std_peer

    if abs(divergence) < 1.5:
        return None

    current = closes[-1]
    fair_value = closes[-period] * (1 + avg_peer)
    move_needed = (fair_value - current) / current

    if divergence > 1.5 and move_needed < -0.01:
        return {
            "direction": "SHORT",
            "divergence": divergence,
            "expected_reversion": move_needed,
            "rationale": f"Divergence: {divergence:+.1f}σ vs peers, expected rev={move_needed:+.1%}",
        }
    elif divergence < -1.5 and move_needed > 0.01:
        return {
            "direction": "LONG",
            "divergence": divergence,
            "expected_reversion": move_needed,
            "rationale": f"Divergence: {divergence:+.1f}σ vs peers, expected rev={move_needed:+.1%}",
        }

    return None


def _compute_rsi_fast(closes: np.ndarray, period: int = 14) -> float:
    """Wilder RSI — lightweight inline version to avoid cross-module import."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _build_dynamic_supply_chain(
    all_bars: dict[str, list[OHLCVBar]],
    corr_threshold: float = 0.50,
) -> dict[str, list[str]]:
    """Dynamically build supply chain map from price correlations.

    Replaces all hardcoded supply chain maps. At runtime, the actual
    Neo4j graph is used; this is the backtesting fallback.
    """
    tickers = list(all_bars.keys())
    if len(tickers) < 2:
        return {}

    returns_map: dict[str, np.ndarray] = {}
    for t, bars in all_bars.items():
        closes = np.array([b.close for b in bars[-60:]])
        if len(closes) > 5:
            returns_map[t] = np.diff(np.log(closes))

    chain_map: dict[str, list[str]] = {}
    ticker_list = list(returns_map.keys())

    for i, t1 in enumerate(ticker_list):
        neighbours: list[tuple[str, float]] = []
        for t2 in ticker_list:
            if t1 == t2:
                continue
            r1, r2 = returns_map[t1], returns_map.get(t2, np.array([]))
            n = min(len(r1), len(r2))
            if n < 8:
                continue
            corr = float(np.corrcoef(r1[-n:], r2[-n:])[0, 1])
            if abs(corr) > corr_threshold:
                neighbours.append((t2, abs(corr)))
        neighbours.sort(key=lambda x: x[1], reverse=True)
        if neighbours:
            chain_map[t1] = [t for t, _ in neighbours[:6]]

    return chain_map


def _discover_pairs_from_data(
    closes_dict: dict[str, np.ndarray],
    corr_threshold: float = 0.60,
    max_pairs: int = 50,
) -> list[tuple[str, str]]:
    """Dynamically discover correlated pairs from available price data.

    Replaces all hardcoded pair lists. Pairs are found by computing
    rolling return correlations across all available tickers.
    """
    tickers = list(closes_dict.keys())
    if len(tickers) < 2:
        return []

    returns_map: dict[str, np.ndarray] = {}
    for t, c in closes_dict.items():
        if len(c) >= 15:
            returns_map[t] = np.diff(np.log(c[-30:])) if len(c) >= 30 else np.diff(np.log(c))

    pairs: list[tuple[str, str, float]] = []
    ticker_list = list(returns_map.keys())

    for i, t1 in enumerate(ticker_list):
        for t2 in ticker_list[i + 1:]:
            r1, r2 = returns_map[t1], returns_map[t2]
            n = min(len(r1), len(r2))
            if n < 10:
                continue
            corr = float(np.corrcoef(r1[-n:], r2[-n:])[0, 1])
            if abs(corr) > corr_threshold:
                pairs.append((t1, t2, abs(corr)))

    pairs.sort(key=lambda x: x[2], reverse=True)
    return [(a, b) for a, b, _ in pairs[:max_pairs]]


def _close_position(pos: BacktestTrade, price: float, date: dt.date) -> None:
    pos.exit_price = price
    pos.exit_date = dt.datetime.combine(date, dt.time())
    if pos.signal.action == TradeAction.LONG:
        gross = (price - pos.entry_price) * pos.shares
    else:
        gross = (pos.entry_price - price) * pos.shares

    costs = equity_costs(
        entry_price=pos.entry_price,
        exit_price=price,
        shares=pos.shares,
    )
    pos.pnl = gross - costs.total
    notional = pos.entry_price * max(pos.shares, 1)
    pos.pnl_pct = (pos.pnl / notional) * 100 if notional > 0 else 0.0
    pos.hold_days = (date - pos.entry_date.date()).days


async def run_backtest(
    tickers: list[str] | None = None,
    initial_equity: float = 1_000_000.0,
    days: int = 60,
    max_position_pct: float = 5.0,
    stop_loss_pct: float = 3.0,
    take_profit_pct: float = 6.0,
    min_conviction: float = 0.55,
    min_composite_score: float = 0.20,
    refresh: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    shock_context: dict[str, Any] | None = None,
    use_baseline: bool = False,
    news_feed: list[Any] | None = None,
    forbidden_strategies: list[str] | None = None,
    strategy_parameters: dict[str, float] | None = None,
    risk_multiplier: float = 1.0,
) -> BacktestResult:
    """Two-track institutional backtest engine.

    Track 1 — INDEX F&O: Trades index futures & options on global indices
              (ES/S&P, NQ/NASDAQ, NIFTY, FDAX, etc.) with proper contract
              multipliers, margin, trailing stops, and partial profit-taking.

    Track 2 — EQUITY INVESTMENT: Long-term stock positions driven by
              supply chain intelligence, deep value, and trend following.

    If refresh=True, runs the full data pipeline first.

    When ``start_date`` / ``end_date`` (ISO strings) are provided the backtest
    targets that explicit window instead of the trailing ``days`` lookback.

    **Autonomous shock detection** — when ``news_feed`` is provided, the
    engine calls `ner_pipeline.shock_context_from_news()` and, if the
    detector fires, that dynamically-generated `causal_intel` dict
    overrides any static `shock_context` passed in. This is the live
    replacement for the hardcoded SHOCK_SCENARIOS catalog.

    **LLM gating** — ``forbidden_strategies`` and ``strategy_parameters``
    are passed through into every MarketContext so the orchestrator's
    Tier-1 gates operate exactly as they would in live trading.
    """
    from execution_engine.math.kelly import (
        kelly_fraction_single, compute_position_size, kelly_options, compute_contracts_size,
    )
    from execution_engine.math.options_pricing import black_scholes, straddle_price, iron_condor_price
    from execution_engine.math.regime import (
        classify_volatility, classify_trend,
        compute_vol_windows, compute_vrp, is_grind_up_regime,
    )
    from execution_engine.math.signals import (
        compute_hurst_exponent, compute_rsi, compute_atr, compute_trend_strength,
        compute_volume_profile,
    )
    from graph_intelligence.agents.strategy_orchestrator import (
        MarketContext, rule_based_orchestrate, naive_baseline_orchestrate,
        INDEX_FUTURES_SPECS, TRADEABLE_INDEX_SYMBOLS,
    )
    orchestrate_fn = naive_baseline_orchestrate if use_baseline else rule_based_orchestrate

    # ── Parse explicit date range ──────────────────────────────────
    _start_dt: dt.date | None = None
    _end_dt: dt.date | None = None
    if start_date:
        _start_dt = dt.date.fromisoformat(start_date)
        _end_dt = dt.date.fromisoformat(end_date) if end_date else dt.date.today()
        _fetch_start = (_start_dt - dt.timedelta(days=30)).isoformat()
        _fetch_end = (_end_dt + dt.timedelta(days=5)).isoformat()
    else:
        _fetch_start = None
        _fetch_end = None

    # ── REFRESH: run full data pipeline if requested ──────────────
    if refresh:
        log.info("Running full data refresh pipeline...")
        refresh_stats = await refresh_all_data(days=max(days + 30, 120))
        log.info(
            f"Refresh done: {refresh_stats['indices_refreshed']} indices, "
            f"{refresh_stats['equities_refreshed']} equities"
        )

    # ── Load equity data ──────────────────────────────────────────
    if tickers is None:
        available = sorted(
            p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0
        )
        if available:
            tickers = available
        else:
            from data_primitives.extractors.universe_builder import GLOBAL_EQUITY_SCREENS
            tickers = (
                GLOBAL_EQUITY_SCREENS.get("US_MEGA_CAP", [])
                + GLOBAL_EQUITY_SCREENS.get("MAJOR_ETFS", [])[:15]
                + GLOBAL_EQUITY_SCREENS.get("SUPPLY_CHAIN_CRITICAL", [])[:10]
            )
            tickers = sorted(set(tickers))

    equity_data: dict[str, list[OHLCVBar]] = {}
    _needs_fetch: list[str] = []
    for ticker in tickers:
        bars = _load_local_bars(ticker)
        if bars:
            if _start_dt:
                overlap = [b for b in bars if _start_dt <= b.timestamp.date() <= _end_dt]
                if overlap:
                    equity_data[ticker] = bars
                else:
                    _needs_fetch.append(ticker)
            else:
                equity_data[ticker] = bars
        else:
            _needs_fetch.append(ticker)

    if _needs_fetch:
        log.info(f"Auto-fetching {len(_needs_fetch)} equity tickers...")
        try:
            from data_primitives.extractors.openbb_client import fetch_multi_ohlcv
            fetched = await fetch_multi_ohlcv(
                _needs_fetch[:50], days=max(days + 30, 120),
                start_date=_fetch_start, end_date=_fetch_end,
            )
            for sym, bars in fetched.items():
                if bars:
                    equity_data[sym] = bars
        except Exception as exc:
            log.warning(f"Equity auto-fetch failed: {exc}")

    # ── Load INDEX data ───────────────────────────────────────────
    index_data: dict[str, list[OHLCVBar]] = {}
    missing_indices: list[str] = []
    for idx_sym in TRADEABLE_INDEX_SYMBOLS:
        bars = _load_index_bars(idx_sym)
        if bars and len(bars) >= 30:
            if _start_dt:
                overlap = [b for b in bars if _start_dt <= b.timestamp.date() <= _end_dt]
                if len(overlap) >= 10:
                    index_data[idx_sym] = bars
                else:
                    missing_indices.append(idx_sym)
            else:
                index_data[idx_sym] = bars
        else:
            missing_indices.append(idx_sym)

    if missing_indices:
        log.info(f"Auto-fetching {len(missing_indices)} world indices...")
        try:
            from data_primitives.extractors.openbb_client import fetch_ohlcv
            for idx_sym in missing_indices:
                try:
                    bars = await fetch_ohlcv(
                        idx_sym, days=max(days + 30, 120),
                        start_date=_fetch_start, end_date=_fetch_end,
                    )
                    if bars and len(bars) >= 20:
                        index_data[idx_sym] = bars
                        if not _start_dt:
                            _save_index_bars(idx_sym, bars)
                        log.info(f"  Fetched {len(bars)} bars for {idx_sym}")
                except Exception as exc:
                    log.debug(f"  Skip {idx_sym}: {exc}")
        except Exception as exc:
            log.warning(f"Index auto-fetch failed: {exc}")

    if _start_dt:
        log.info(
            f"Starting backtest: {len(equity_data)} equities + {len(index_data)} indices, "
            f"{_start_dt} → {_end_dt}, ${initial_equity:,.0f}"
        )
    else:
        log.info(
            f"Starting backtest: {len(equity_data)} equities + {len(index_data)} indices, "
            f"{days} days, ${initial_equity:,.0f}"
        )

    all_price_data = {**equity_data, **index_data}
    if not all_price_data:
        log.warning("No data available — run `ayvid data ingest` first")
        return BacktestResult(
            start_date=dt.datetime.now(dt.UTC),
            end_date=dt.datetime.now(dt.UTC),
            initial_equity=initial_equity,
            final_equity=initial_equity,
        )

    all_dates: set[dt.date] = set()
    for bars in all_price_data.values():
        for bar in bars:
            all_dates.add(bar.timestamp.date())

    if _start_dt:
        sorted_dates = sorted(d for d in all_dates if _start_dt <= d <= _end_dt)
    else:
        sorted_dates = sorted(all_dates)[-days:]

    if not sorted_dates:
        return BacktestResult(
            start_date=dt.datetime.now(dt.UTC),
            end_date=dt.datetime.now(dt.UTC),
            initial_equity=initial_equity,
            final_equity=initial_equity,
        )

    cash = initial_equity
    equity_positions: list[BacktestTrade] = []
    fno_trades: list[FnOTrade] = []
    closed_equity: list[BacktestTrade] = []
    equity_curve: list[float] = [initial_equity]
    equity_dates: list[str] = [sorted_dates[0].isoformat()]
    signals_generated = 0
    signals_filtered = 0
    shocks_analyzed = 0
    fno_capital_at_risk = 0.0
    FNO_CAPITAL_CAP = 0.30
    _settled_fno: set[str] = set()
    r_distribution: dict[str, list[float]] = {"accepted": [], "rejected": [], "realized": []}
    cost_breakdown: dict[str, float] = {
        "commission_total": 0.0,
        "slippage_total": 0.0,
        "cost_total": 0.0,
        "cost_count": 0,
    }
    _idx_consec_losses: dict[str, int] = {}
    _idx_cooldown_until: dict[str, dt.date] = {}

    def _get_bars_up_to(data: dict[str, list[OHLCVBar]], ticker: str, date: dt.date) -> list[OHLCVBar]:
        return [b for b in data.get(ticker, []) if b.timestamp.date() <= date]

    # ── Autonomous shock detection: if a news feed is supplied, run the
    # NER-based detector and let any dynamic signal override the static
    # `shock_context` argument. This is the live replacement for the
    # hardcoded SHOCK_SCENARIOS catalog (Step 4 + Step 5).
    if news_feed:
        try:
            from data_primitives.extractors.ner_pipeline import shock_context_from_news
            auto_ctx = shock_context_from_news(news_feed)
            if auto_ctx:
                log.info(
                    f"[AUTO-SHOCK] detector fired — overriding shock_context "
                    f"(type={auto_ctx['causal_intel'].get('shock_type')}, "
                    f"score={auto_ctx['causal_intel'].get('shock_score'):.1f}, "
                    f"bias={auto_ctx['causal_intel'].get('expected_direction')})"
                )
                shock_context = auto_ctx
        except Exception as exc:
            log.warning(f"Autonomous shock detection failed, using static context: {exc}")

    _shock_tickers: set[str] = set()
    _shock_active = False
    _shock_source = ""
    _shock_magnitude = 0.0
    _index_bias: dict[str, str] = {}
    _beneficiary_tickers: set[str] = set()
    _victim_tickers: set[str] = set()
    _forbidden_strategies: list[str] = list(forbidden_strategies or [])
    _strategy_parameters: dict[str, float] = dict(strategy_parameters or {})
    _risk_multiplier: float = float(risk_multiplier or 1.0)
    if shock_context:
        _shock_active = shock_context.get("supply_chain_shock", False)
        _shock_source = shock_context.get("shock_source", "")
        _shock_tickers = shock_context.get("shock_tickers", set())
        _shock_magnitude = shock_context.get("shock_magnitude", -0.08)
        _index_bias = shock_context.get("index_bias", {})
        _beneficiary_tickers = shock_context.get("beneficiary_tickers", set())
        _victim_tickers = shock_context.get("victim_tickers", set())

    def _build_ctx(ticker: str, bars_list: list[OHLCVBar], asset_class: str) -> MarketContext | None:
        """Build MarketContext from OHLCV bars."""
        if len(bars_list) < 30:
            return None
        closes = np.array([b.close for b in bars_list])
        highs = np.array([b.high for b in bars_list])
        lows = np.array([b.low for b in bars_list])
        volumes = np.array([b.volume for b in bars_list], dtype=float)

        price = float(closes[-1])
        log_returns = np.diff(np.log(closes[-31:])) if len(closes) > 31 else np.diff(np.log(closes))

        hurst = compute_hurst_exponent(log_returns)
        rsi = compute_rsi(closes, 14)
        atr_val = compute_atr(highs, lows, closes, 14)
        trend = compute_trend_strength(closes, short=10, long=30)
        vol_ratio = compute_volume_profile(volumes, 20)

        vol_regime, rv, vol_pct = classify_volatility(log_returns)
        trend_regime, adx = classify_trend(highs, lows, closes, hurst)

        z_score = 0.0
        if len(closes) >= 20:
            mu = float(np.mean(closes[-20:]))
            sd = float(np.std(closes[-20:], ddof=1))
            if sd > 1e-10:
                z_score = (price - mu) / sd

        ret_5d = float(closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
        ret_20d = float(closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0

        iv_proxy = rv + abs(ret_5d) * 2.0 + 0.02
        iv_rv_spread = iv_proxy - rv if rv > 0 else None

        # ── Tier-1 regime gating inputs: VRP + grind-up filter ──
        rv_short, rv_long, vol_compression = compute_vol_windows(
            log_returns, short=20, long=60,
        )
        vrp_val = compute_vrp(iv_proxy, rv_short) if iv_rv_spread is not None else 0.0
        grind_up = is_grind_up_regime(
            vol_percentile=vol_pct,
            vol_compression=vol_compression,
            ret_20d=ret_20d,
            adx=adx,
            hurst=hurst,
            vrp=vrp_val,
        )

        is_shock_ticker = _shock_active and ticker in _shock_tickers
        shock_mag = _shock_magnitude if is_shock_ticker else 0.0
        # Rolling 5-bar window for shock magnitude so early bars still register
        if is_shock_ticker and len(closes) >= 6:
            window = min(5, len(closes) - 1)
            shock_mag = float(closes[-1] / closes[-1 - window] - 1)

        # For indices, use index_bias from causal_intel; for equities, classify as beneficiary/victim
        idx_bias = _index_bias.get(ticker, "") if asset_class == "INDEX" else ""
        is_benef = _shock_active and ticker in _beneficiary_tickers
        is_vict = _shock_active and ticker in _victim_tickers

        spec = INDEX_FUTURES_SPECS.get(ticker, {})
        return MarketContext(
            ticker=ticker,
            current_price=price,
            volatility_regime=vol_regime.value,
            trend_regime=trend_regime.value,
            hurst=hurst,
            rsi=rsi,
            atr=atr_val,
            realized_vol=rv,
            implied_vol=iv_proxy if iv_rv_spread is not None else None,
            iv_rv_spread=iv_rv_spread,
            z_score=z_score,
            trend_strength=trend,
            volume_ratio=vol_ratio,
            ret_5d=ret_5d,
            ret_20d=ret_20d,
            asset_class=asset_class,
            country=spec.get("country", "US"),
            supply_chain_shock=is_shock_ticker or _shock_active,
            shock_source=_shock_source if _shock_active else None,
            shock_magnitude=shock_mag,
            shock_bias=idx_bias,
            is_beneficiary=is_benef,
            is_victim=is_vict,
            vrp=vrp_val,
            vol_compression=vol_compression,
            grind_up_flag=grind_up,
            # Causal conviction only arrives via the live LangGraph path;
            # in backtest we default to 0 so the grind-up gate holds.
            causal_conviction=0.0,
            # Strategist-emitted blocklist + Supervisor-emitted dynamic
            # thresholds. In offline walk-forwards these are typically
            # empty / default; live and scenario runs populate them.
            forbidden_strategies=_forbidden_strategies,
            strategy_parameters=_strategy_parameters,
            risk_multiplier=_risk_multiplier,
        )

    for date in sorted_dates:
        day_idx = sorted_dates.index(date)
        open_new = day_idx < len(sorted_dates) - 5

        # ── Manage existing equity positions ──
        date_eq_prices: dict[str, float] = {}
        for ticker, bars in equity_data.items():
            bars_to = _get_bars_up_to(equity_data, ticker, date)
            if bars_to:
                date_eq_prices[ticker] = bars_to[-1].close

        to_close: list[int] = []
        for i, pos in enumerate(equity_positions):
            if pos.signal.ticker not in date_eq_prices:
                continue
            current_price = date_eq_prices[pos.signal.ticker]

            if pos.signal.action == TradeAction.LONG:
                unrealized = (current_price - pos.entry_price) / pos.entry_price
                if unrealized > 0.02:
                    new_sl = current_price - (current_price - pos.entry_price) * 0.4
                    pos.stop_loss_price = max(pos.stop_loss_price, new_sl)
                if current_price <= pos.stop_loss_price:
                    _close_position(pos, current_price, date)
                    to_close.append(i)
                elif current_price >= pos.take_profit_price:
                    _close_position(pos, current_price, date)
                    to_close.append(i)
            else:
                unrealized = (pos.entry_price - current_price) / pos.entry_price
                if unrealized > 0.02:
                    new_sl = current_price + (pos.entry_price - current_price) * 0.4
                    pos.stop_loss_price = min(pos.stop_loss_price, new_sl)
                if current_price >= pos.stop_loss_price:
                    _close_position(pos, current_price, date)
                    to_close.append(i)
                elif current_price <= pos.take_profit_price:
                    _close_position(pos, current_price, date)
                    to_close.append(i)

            max_hold = 20
            hold = (date - pos.entry_date.date()).days
            if hold > max_hold and i not in to_close:
                _close_position(pos, current_price, date)
                to_close.append(i)

        for i in sorted(set(to_close), reverse=True):
            closed_equity.append(equity_positions.pop(i))
            t = closed_equity[-1]
            cash += t.shares * (t.exit_price or t.entry_price)

        if not open_new:
            # Last 5 days: only manage existing, don't open new
            position_value = sum(
                p.shares * date_eq_prices.get(p.signal.ticker, p.entry_price)
                for p in equity_positions
            )
            equity_curve.append(cash + position_value)
            equity_dates.append(date.isoformat())
            continue

        # ══════════════════════════════════════════════════════════
        #  TRACK 1: INDEX F&O — futures and options on world indices
        # ══════════════════════════════════════════════════════════
        for idx_sym, idx_bars in index_data.items():
            idx_bars_to_date = _get_bars_up_to(index_data, idx_sym, date)
            ctx = _build_ctx(idx_sym, idx_bars_to_date, "INDEX")
            if ctx is None:
                continue

            shocks_analyzed += 1
            decision = orchestrate_fn(ctx)

            if decision.action == "SKIP":
                signals_filtered += 1
                continue
            fno_min_conviction = max(min_conviction, 0.70)
            if decision.conviction < fno_min_conviction:
                signals_filtered += 1
                continue
            if decision.expected_r < decision.min_r:
                signals_filtered += 1
                r_distribution["rejected"].append(decision.expected_r)
                continue

            r_distribution["accepted"].append(decision.expected_r)
            signals_generated += 1

            spec = INDEX_FUTURES_SPECS.get(idx_sym, {})
            multiplier = spec.get("multiplier", 50)
            usd_rate = spec.get("usd_rate", 1.0)
            margin_pct = spec.get("margin_pct", 0.05)
            future_name = spec.get("future", idx_sym)
            country = spec.get("country", "")
            fut_expiry_rule = spec.get("fut_expiry_rule", "3rd_fri")
            opt_expiry_rule = spec.get("opt_expiry_rule", "weekly_fri")
            price = ctx.current_price
            atr_val = ctx.atr
            rv = ctx.realized_vol

            # Per-index cooldown: skip after 2 consecutive losses
            if _idx_cooldown_until.get(idx_sym, dt.date.min) > date:
                signals_filtered += 1
                continue

            # F&O capital cap: max 50% of portfolio in F&O at risk
            fno_cap = cash * FNO_CAPITAL_CAP
            available_capital = max(min(cash - fno_capital_at_risk, fno_cap - fno_capital_at_risk), 0.0)
            if available_capital < 1000:
                continue

            # ── Intraday vs BTST gate ────────────────────────────
            # Default: intraday (exit same day). BTST only if conviction >= 0.80.
            BTST_CONVICTION = 0.80
            allow_btst = decision.conviction >= BTST_CONVICTION

            # ── INDEX_FUTURE: real expiry, 2% risk lot sizing ──
            if decision.instrument_type == "INDEX_FUTURE":
                expiry_date = _next_expiry(date, fut_expiry_rule)
                days_to_expiry = (expiry_date - date).days
                if days_to_expiry < 2:
                    expiry_date = _next_expiry(date + dt.timedelta(days=1), fut_expiry_rule)
                    days_to_expiry = (expiry_date - date).days

                if allow_btst:
                    exit_idx = None
                    for ei in range(day_idx + 1, len(sorted_dates)):
                        if sorted_dates[ei] >= expiry_date:
                            exit_idx = ei
                            break
                    if exit_idx is None or exit_idx >= len(sorted_dates):
                        continue
                else:
                    exit_idx = day_idx + 1 if day_idx + 1 < len(sorted_dates) else None
                    if exit_idx is None:
                        continue
                hold_d = exit_idx - day_idx

                r_rate = 0.05
                carry = price * r_rate * (days_to_expiry / 365.0)
                fut_entry_price = price + carry

                stop_dist = atr_val * 2.0
                target_dist = atr_val * 3.5

                lots = _compute_lots_from_risk(available_capital, stop_dist, multiplier, usd_rate)
                notional_1lot = fut_entry_price * multiplier * usd_rate
                margin_1lot = notional_1lot * margin_pct
                total_margin = margin_1lot * lots
                if total_margin > available_capital * MAX_PORTFOLIO_RISK_PCT:
                    lots = max(1, int(available_capital * MAX_PORTFOLIO_RISK_PCT / max(margin_1lot, 1.0)))
                    total_margin = margin_1lot * lots
                if lots < 1:
                    continue

                spot_entry = price
                peak_pnl = 0.0
                stop_level = stop_dist
                fut_exit_price = fut_entry_price
                spot_exit = price
                actual_hold = hold_d
                exit_reason = "eod_close" if not allow_btst else "expiry"
                ctc_triggered = False
                trade_mode = "BTST" if allow_btst else "INTRADAY"

                for check_idx in range(day_idx + 1, min(exit_idx + 1, len(sorted_dates))):
                    check_date = sorted_dates[check_idx]
                    check_bars = _get_bars_up_to(index_data, idx_sym, check_date)
                    if not check_bars:
                        continue
                    mid_spot = check_bars[-1].close
                    dte_remaining = max((expiry_date - check_date).days, 0)
                    mid_fut = mid_spot + mid_spot * r_rate * (dte_remaining / 365.0)

                    if decision.direction == "LONG":
                        pnl_pts = mid_fut - fut_entry_price
                    else:
                        pnl_pts = fut_entry_price - mid_fut

                    # Hard -1R stop: never let loss exceed initial risk
                    if pnl_pts <= -stop_dist:
                        fut_exit_price = mid_fut
                        spot_exit = mid_spot
                        actual_hold = check_idx - day_idx
                        exit_reason = "hard_1R_stop"
                        break

                    peak_pnl = max(peak_pnl, pnl_pts)

                    if pnl_pts >= atr_val:
                        stop_level = min(stop_level, 0.0)
                        ctc_triggered = True
                    if ctc_triggered and peak_pnl > atr_val:
                        trail = peak_pnl * 0.50
                        stop_level = min(stop_level, peak_pnl - trail)

                    if pnl_pts <= -stop_level and stop_level > 0:
                        fut_exit_price = mid_fut
                        spot_exit = mid_spot
                        actual_hold = check_idx - day_idx
                        exit_reason = "trailing_stop" if ctc_triggered else "initial_stop"
                        break
                    if pnl_pts < 0 and ctc_triggered:
                        fut_exit_price = mid_fut
                        spot_exit = mid_spot
                        actual_hold = check_idx - day_idx
                        exit_reason = "ctc_stop"
                        break
                    if pnl_pts >= target_dist:
                        fut_exit_price = mid_fut
                        spot_exit = mid_spot
                        actual_hold = check_idx - day_idx
                        exit_reason = "profit_target"
                        break
                else:
                    exp_bars = _get_bars_up_to(index_data, idx_sym, sorted_dates[min(exit_idx, len(sorted_dates) - 1)])
                    if exp_bars:
                        spot_exit = exp_bars[-1].close
                        fut_exit_price = spot_exit

                if decision.direction == "LONG":
                    pnl_pts = fut_exit_price - fut_entry_price
                else:
                    pnl_pts = fut_entry_price - fut_exit_price

                pnl_usd = pnl_pts * multiplier * usd_rate * lots
                risk_usd = stop_dist * multiplier * usd_rate * lots

                fut_costs = future_costs(
                    underlying=idx_sym,
                    entry_price=fut_entry_price,
                    exit_price=fut_exit_price,
                    lots=lots,
                    multiplier=multiplier,
                    usd_rate=usd_rate,
                )
                pnl_usd -= fut_costs.total
                cost_breakdown["commission_total"] += fut_costs.commission
                cost_breakdown["slippage_total"] += fut_costs.slippage_entry + fut_costs.slippage_exit
                cost_breakdown["cost_total"] += fut_costs.total
                cost_breakdown["cost_count"] += 1

                realized_r = pnl_usd / max(risk_usd, 0.01)
                # Defensive -1R cap — hard stop should enforce this in the sim loop
                if realized_r < -1.0:
                    pnl_usd = -risk_usd
                    realized_r = -1.0
                r_distribution["realized"].append(realized_r)

                fno_capital_at_risk += total_margin
                actual_exit_date = sorted_dates[min(day_idx + actual_hold, len(sorted_dates) - 1)]

                fno_trades.append(FnOTrade(
                    trade_id=f"bt_{date}_{future_name}_fut",
                    strategy=decision.strategy_name,
                    underlying=idx_sym,
                    instrument="INDEX_FUTURE",
                    direction=decision.direction,
                    entry_date=date,
                    exit_date=actual_exit_date,
                    entry_cost=round(total_margin, 2),
                    exit_value=round(total_margin + pnl_usd, 2),
                    pnl=round(pnl_usd, 2),
                    pnl_pct=round(pnl_usd / max(total_margin, 0.01) * 100, 2),
                    hold_days=actual_hold,
                    details={
                        "contract": future_name,
                        "index_name": spec.get("name", ""),
                        "country": country,
                        "exchange": spec.get("exchange", ""),
                        "multiplier": multiplier,
                        "lots": lots,
                        "currency": spec.get("currency", "USD"),
                        "trade_mode": trade_mode,
                        "spot_entry": round(spot_entry, 2),
                        "spot_exit": round(spot_exit, 2),
                        "fut_entry": round(fut_entry_price, 2),
                        "fut_exit": round(fut_exit_price, 2),
                        "pnl_pts": round(pnl_pts, 2),
                        "margin_per_lot": round(margin_1lot, 0),
                        "notional_usd": round(notional_1lot * lots, 0),
                        "stop_loss_pts": round(stop_dist, 2),
                        "risk_1R_usd": round(risk_usd, 2),
                        "expiry_date": expiry_date.isoformat(),
                        "dte_at_entry": days_to_expiry,
                        "expected_r": round(decision.expected_r, 2),
                        "realized_r": round(realized_r, 2),
                        "exit_reason": exit_reason,
                        "conviction": round(decision.conviction, 3),
                        "rationale": decision.rationale,
                        "graph_intel": decision.graph_intel,
                    },
                ))

            # ── INDEX_CALL / INDEX_PUT: real weekly/monthly expiry, 2% risk sizing ──
            elif decision.instrument_type in ("INDEX_CALL", "INDEX_PUT"):
                expiry_date = _next_weekly_expiry(date, opt_expiry_rule)
                days_to_expiry = (expiry_date - date).days
                if days_to_expiry < 2:
                    expiry_date = _next_weekly_expiry(date + dt.timedelta(days=2), opt_expiry_rule)
                    days_to_expiry = (expiry_date - date).days
                if days_to_expiry < 2:
                    continue

                if allow_btst:
                    exit_idx = None
                    for ei in range(day_idx + 1, len(sorted_dates)):
                        if sorted_dates[ei] >= expiry_date:
                            exit_idx = ei
                            break
                    if exit_idx is None or exit_idx >= len(sorted_dates):
                        continue
                else:
                    exit_idx = day_idx + 1 if day_idx + 1 < len(sorted_dates) else None
                    if exit_idx is None:
                        continue
                hold_d = exit_idx - day_idx

                T = days_to_expiry / 252.0
                r_rate = 0.05
                sigma = max(rv, 0.12)
                strike = decision.strike or price
                opt_type = "CALL" if "CALL" in decision.instrument_type else "PUT"

                entry_premium = black_scholes(price, strike, T, r_rate, sigma, opt_type)
                if entry_premium < 0.5:
                    continue

                stop_dist_pts = entry_premium * 0.60
                lots = _compute_lots_from_risk(available_capital, stop_dist_pts, multiplier, usd_rate)
                dte_scale = min(1.0, days_to_expiry / 5.0)
                lots = max(1, int(lots * dte_scale))
                cost_1lot = entry_premium * multiplier * usd_rate
                total_cost = cost_1lot * lots
                if total_cost > available_capital * MAX_PORTFOLIO_RISK_PCT:
                    lots = max(1, int(available_capital * MAX_PORTFOLIO_RISK_PCT / max(cost_1lot, 1.0)))
                    total_cost = cost_1lot * lots
                if lots < 1:
                    continue

                spot_entry = price
                peak_prem = entry_premium
                stop_prem = entry_premium * 0.40
                exit_premium = entry_premium
                spot_exit = price
                actual_hold = hold_d
                exit_reason = "eod_close" if not allow_btst else "expiry"
                trade_mode = "BTST" if allow_btst else "INTRADAY"

                for check_idx in range(day_idx + 1, min(exit_idx + 1, len(sorted_dates))):
                    check_date = sorted_dates[check_idx]
                    check_bars = _get_bars_up_to(index_data, idx_sym, check_date)
                    if not check_bars:
                        continue
                    S_mid = check_bars[-1].close
                    dte_remaining = max((expiry_date - check_date).days, 0)
                    T_mid = max(dte_remaining, 1) / 252.0
                    mid_prem = black_scholes(S_mid, strike, T_mid, r_rate, sigma, opt_type)

                    # Hard -1R stop: exit if premium loss >= initial risk
                    loss_pct = (entry_premium - mid_prem) / max(entry_premium, 0.01)
                    if loss_pct >= 0.60:
                        exit_premium = mid_prem
                        spot_exit = S_mid
                        actual_hold = check_idx - day_idx
                        exit_reason = "hard_1R_stop"
                        break

                    profit_pct = (mid_prem - entry_premium) / max(entry_premium, 0.01)
                    peak_prem = max(peak_prem, mid_prem)

                    if profit_pct >= 0.30:
                        stop_prem = max(stop_prem, entry_premium)
                    if peak_prem > entry_premium * 1.5:
                        stop_prem = max(stop_prem, entry_premium + (peak_prem - entry_premium) * 0.50)

                    if profit_pct >= 0.80:
                        exit_premium = mid_prem
                        spot_exit = S_mid
                        actual_hold = check_idx - day_idx
                        exit_reason = "profit_target"
                        break
                    if mid_prem <= stop_prem:
                        exit_premium = mid_prem
                        spot_exit = S_mid
                        actual_hold = check_idx - day_idx
                        exit_reason = "trailing_stop" if stop_prem >= entry_premium else "initial_stop"
                        break
                else:
                    exp_bars = _get_bars_up_to(index_data, idx_sym, sorted_dates[min(exit_idx, len(sorted_dates) - 1)])
                    if exp_bars:
                        S_exit = exp_bars[-1].close
                        spot_exit = S_exit
                        if opt_type == "CALL":
                            exit_premium = max(S_exit - strike, 0)
                        else:
                            exit_premium = max(strike - S_exit, 0)

                pnl_usd = (exit_premium - entry_premium) * multiplier * usd_rate * lots
                risk_usd = stop_dist_pts * multiplier * usd_rate * lots

                opt_costs = option_costs(
                    underlying=idx_sym,
                    entry_premium=entry_premium,
                    exit_premium=exit_premium,
                    lots=lots,
                    multiplier=multiplier,
                    usd_rate=usd_rate,
                )
                pnl_usd -= opt_costs.total
                cost_breakdown["commission_total"] += opt_costs.commission
                cost_breakdown["slippage_total"] += opt_costs.slippage_entry + opt_costs.slippage_exit
                cost_breakdown["cost_total"] += opt_costs.total
                cost_breakdown["cost_count"] += 1

                realized_r = pnl_usd / max(risk_usd, 0.01)
                if realized_r < -1.0:
                    pnl_usd = -risk_usd
                    realized_r = -1.0
                r_distribution["realized"].append(realized_r)
                fno_capital_at_risk += total_cost

                actual_exit_date = sorted_dates[min(day_idx + actual_hold, len(sorted_dates) - 1)]

                fno_trades.append(FnOTrade(
                    trade_id=f"bt_{date}_{future_name}_{opt_type.lower()}",
                    strategy=decision.strategy_name,
                    underlying=idx_sym,
                    instrument=f"INDEX_{opt_type}",
                    direction=decision.direction,
                    entry_date=date,
                    exit_date=actual_exit_date,
                    entry_cost=round(total_cost, 2),
                    exit_value=round(total_cost + pnl_usd, 2),
                    pnl=round(pnl_usd, 2),
                    pnl_pct=round(pnl_usd / max(total_cost, 0.01) * 100, 2),
                    hold_days=actual_hold,
                    details={
                        "contract": f"{future_name} {strike:.0f}{opt_type[0]}E",
                        "index_name": spec.get("name", ""),
                        "country": country,
                        "exchange": spec.get("exchange", ""),
                        "opt_type": opt_type,
                        "strike": round(strike, 2),
                        "lots": lots,
                        "multiplier": multiplier,
                        "currency": spec.get("currency", "USD"),
                        "trade_mode": trade_mode,
                        "spot_entry": round(spot_entry, 2),
                        "spot_exit": round(spot_exit, 2),
                        "entry_premium": round(entry_premium, 2),
                        "exit_premium": round(exit_premium, 2),
                        "iv": round(sigma, 4),
                        "cost_per_lot": round(cost_1lot, 2),
                        "stop_loss_premium": round(stop_prem, 2),
                        "risk_1R_usd": round(risk_usd, 2),
                        "expiry_date": expiry_date.isoformat(),
                        "dte_at_entry": days_to_expiry,
                        "expected_r": round(decision.expected_r, 2),
                        "realized_r": round(realized_r, 2),
                        "exit_reason": exit_reason,
                        "conviction": round(decision.conviction, 3),
                        "rationale": decision.rationale,
                        "graph_intel": decision.graph_intel,
                    },
                ))

            # ── INDEX_SPREAD: real expiry, 2% risk lot sizing ──
            elif decision.instrument_type == "INDEX_SPREAD":
                expiry_date = _next_weekly_expiry(date, opt_expiry_rule)
                days_to_expiry = (expiry_date - date).days
                if days_to_expiry < 2:
                    expiry_date = _next_weekly_expiry(date + dt.timedelta(days=2), opt_expiry_rule)
                    days_to_expiry = (expiry_date - date).days
                if days_to_expiry < 2:
                    continue

                if allow_btst:
                    exit_idx = None
                    for ei in range(day_idx + 1, len(sorted_dates)):
                        if sorted_dates[ei] >= expiry_date:
                            exit_idx = ei
                            break
                    if exit_idx is None or exit_idx >= len(sorted_dates):
                        continue
                else:
                    exit_idx = day_idx + 1 if day_idx + 1 < len(sorted_dates) else None
                    if exit_idx is None:
                        continue
                hold_d = exit_idx - day_idx

                r_rate = 0.05
                sigma = max(rv, 0.12)
                leg = decision.second_leg or {}
                long_K = decision.strike or price
                short_K = leg.get("strike", price * 1.03)
                opt_type = leg.get("type", "CALL")

                spread_width = abs(short_K - long_K)
                T_entry = days_to_expiry / 252.0
                long_prem = black_scholes(price, long_K, T_entry, r_rate, sigma, opt_type)
                short_prem = black_scholes(price, short_K, T_entry, r_rate, sigma, opt_type)
                net_debit = max(long_prem - short_prem, spread_width * 0.10)

                if net_debit < 0.5:
                    continue

                stop_dist_pts = net_debit * 0.60
                lots = _compute_lots_from_risk(available_capital, stop_dist_pts, multiplier, usd_rate)
                dte_scale = min(1.0, days_to_expiry / 5.0)
                lots = max(1, int(lots * dte_scale))
                cost_1lot = net_debit * multiplier * usd_rate
                total_cost = cost_1lot * lots
                if total_cost > available_capital * MAX_PORTFOLIO_RISK_PCT:
                    lots = max(1, int(available_capital * MAX_PORTFOLIO_RISK_PCT / max(cost_1lot, 1.0)))
                    total_cost = cost_1lot * lots
                if lots < 1:
                    continue

                max_profit_pts = spread_width - net_debit
                spot_entry = price
                peak_spread_val = net_debit
                stop_spread_val = net_debit * 0.40
                exit_spread_val = net_debit
                spot_exit = price
                locked_pnl_pts = 0.0
                qty_remaining = 1.0
                exit_reason = "eod_close" if not allow_btst else "expiry"
                actual_hold = hold_d
                trade_mode = "BTST" if allow_btst else "INTRADAY"

                for check_idx in range(day_idx + 1, min(exit_idx + 1, len(sorted_dates))):
                    check_date = sorted_dates[check_idx]
                    check_bars = _get_bars_up_to(index_data, idx_sym, check_date)
                    if not check_bars:
                        continue
                    S_mid = check_bars[-1].close
                    dte_remaining = max((expiry_date - check_date).days, 0)
                    T_mid = max(dte_remaining, 1) / 252.0

                    lv = black_scholes(S_mid, long_K, T_mid, r_rate, sigma, opt_type)
                    sv = black_scholes(S_mid, short_K, T_mid, r_rate, sigma, opt_type)
                    mid_spread = max(lv - sv, 0.0)

                    # Hard -1R stop: if spread value drops by stop_dist (60% of debit), exit
                    loss_from_debit = net_debit - mid_spread
                    if loss_from_debit >= stop_dist_pts:
                        exit_spread_val = mid_spread
                        spot_exit = S_mid
                        locked_pnl_pts += (mid_spread - net_debit) * qty_remaining
                        qty_remaining = 0.0
                        actual_hold = check_idx - day_idx
                        exit_reason = "hard_1R_stop"
                        break

                    profit_pct = (mid_spread - net_debit) / max(net_debit, 0.01)
                    peak_spread_val = max(peak_spread_val, mid_spread)

                    if profit_pct >= 0.30:
                        stop_spread_val = max(stop_spread_val, net_debit)
                    if profit_pct >= 0.50 and qty_remaining > 0.75:
                        locked_pnl_pts += (mid_spread - net_debit) * 0.50
                        qty_remaining = 0.50
                    if qty_remaining <= 0.50 and peak_spread_val > net_debit:
                        stop_spread_val = max(stop_spread_val, net_debit + (peak_spread_val - net_debit) * 0.50)
                    if mid_spread >= net_debit + max_profit_pts * 0.75:
                        exit_spread_val = mid_spread
                        spot_exit = S_mid
                        locked_pnl_pts += (mid_spread - net_debit) * qty_remaining
                        qty_remaining = 0.0
                        actual_hold = check_idx - day_idx
                        exit_reason = "profit_target"
                        break
                    if mid_spread <= stop_spread_val:
                        exit_spread_val = mid_spread
                        spot_exit = S_mid
                        locked_pnl_pts += (mid_spread - net_debit) * qty_remaining
                        qty_remaining = 0.0
                        actual_hold = check_idx - day_idx
                        exit_reason = "trailing_stop" if stop_spread_val >= net_debit else "initial_stop"
                        break
                else:
                    exp_bars = _get_bars_up_to(index_data, idx_sym, sorted_dates[min(exit_idx, len(sorted_dates) - 1)])
                    if exp_bars:
                        S_exit = exp_bars[-1].close
                        spot_exit = S_exit
                        if opt_type == "CALL":
                            payoff = max(S_exit - long_K, 0) - max(S_exit - short_K, 0)
                        else:
                            payoff = max(long_K - S_exit, 0) - max(short_K - S_exit, 0)
                        exit_spread_val = payoff
                        locked_pnl_pts += (payoff - net_debit) * qty_remaining

                pnl_usd = locked_pnl_pts * multiplier * usd_rate * lots
                risk_usd = stop_dist_pts * multiplier * usd_rate * lots

                spr_costs = spread_costs(
                    underlying=idx_sym,
                    net_debit=net_debit,
                    exit_value=exit_spread_val,
                    lots=lots,
                    multiplier=multiplier,
                    usd_rate=usd_rate,
                )
                pnl_usd -= spr_costs.total
                cost_breakdown["commission_total"] += spr_costs.commission
                cost_breakdown["slippage_total"] += spr_costs.slippage_entry + spr_costs.slippage_exit
                cost_breakdown["cost_total"] += spr_costs.total
                cost_breakdown["cost_count"] += 1

                realized_r = pnl_usd / max(risk_usd, 0.01)
                if realized_r < -1.0:
                    pnl_usd = -risk_usd
                    realized_r = -1.0
                r_distribution["realized"].append(realized_r)
                fno_capital_at_risk += total_cost

                actual_exit_date = sorted_dates[min(day_idx + actual_hold, len(sorted_dates) - 1)]

                fno_trades.append(FnOTrade(
                    trade_id=f"bt_{date}_{future_name}_spread",
                    strategy=decision.strategy_name,
                    underlying=idx_sym,
                    instrument="INDEX_SPREAD",
                    direction=decision.direction,
                    entry_date=date,
                    exit_date=actual_exit_date,
                    entry_cost=round(total_cost, 2),
                    exit_value=round(total_cost + pnl_usd, 2),
                    pnl=round(pnl_usd, 2),
                    pnl_pct=round(pnl_usd / max(total_cost, 0.01) * 100, 2),
                    hold_days=actual_hold,
                    details={
                        "contract": f"{future_name} {long_K:.0f}/{short_K:.0f} {opt_type[0]}E",
                        "index_name": spec.get("name", ""),
                        "country": country,
                        "exchange": spec.get("exchange", ""),
                        "opt_type": opt_type,
                        "long_strike": round(long_K, 2),
                        "short_strike": round(short_K, 2),
                        "spread_width": round(spread_width, 2),
                        "lots": lots,
                        "multiplier": multiplier,
                        "currency": spec.get("currency", "USD"),
                        "trade_mode": trade_mode,
                        "long_prem": round(long_prem, 2),
                        "short_prem": round(short_prem, 2),
                        "net_debit": round(net_debit, 2),
                        "exit_spread_val": round(exit_spread_val, 2),
                        "max_profit_pts": round(max_profit_pts, 2),
                        "spot_entry": round(spot_entry, 2),
                        "spot_exit": round(spot_exit, 2),
                        "iv": round(sigma, 4),
                        "cost_per_lot": round(cost_1lot, 2),
                        "stop_loss_pts": round(stop_dist_pts, 2),
                        "risk_1R_usd": round(risk_usd, 2),
                        "expiry_date": expiry_date.isoformat(),
                        "dte_at_entry": days_to_expiry,
                        "expected_r": round(decision.expected_r, 2),
                        "realized_r": round(realized_r, 2),
                        "exit_reason": exit_reason,
                        "conviction": round(decision.conviction, 3),
                        "rationale": decision.rationale,
                        "graph_intel": decision.graph_intel,
                    },
                ))

        # ══════════════════════════════════════════════════════════
        #  TRACK 2: EQUITY INVESTMENT — stock positions only
        # ══════════════════════════════════════════════════════════
        for ticker, bars in equity_data.items():
            bars_to_date = _get_bars_up_to(equity_data, ticker, date)
            ctx = _build_ctx(ticker, bars_to_date, "EQUITY")
            if ctx is None:
                continue

            shocks_analyzed += 1
            decision = orchestrate_fn(ctx)

            if decision.action == "SKIP":
                signals_filtered += 1
                continue
            if decision.conviction < min_conviction:
                signals_filtered += 1
                continue
            if decision.expected_r < decision.min_r:
                signals_filtered += 1
                r_distribution["rejected"].append(decision.expected_r)
                continue

            if decision.instrument_type != "EQUITY":
                continue

            r_distribution["accepted"].append(decision.expected_r)
            signals_generated += 1

            if any(p.signal.ticker == ticker for p in equity_positions):
                continue

            price = ctx.current_price
            atr_val = ctx.atr
            atr_mult = 3.0
            if decision.direction == "LONG":
                sl_price = price - atr_val * atr_mult
                tp_price = price + atr_val * atr_mult * 1.5
                action = TradeAction.LONG
            else:
                sl_price = price + atr_val * atr_mult
                tp_price = price - atr_val * atr_mult * 1.5
                action = TradeAction.SHORT

            rr = abs(tp_price - price) / max(abs(sl_price - price), 0.01)
            frac = kelly_fraction_single(decision.conviction, max(rr, 0.3))
            frac = min(frac, max_position_pct / 100.0)

            shares = compute_position_size(cash, frac, price)
            if shares > 0 and price * shares < cash * 0.80:
                cash -= shares * price
                signal = TradeSignal(
                    signal_id=f"bt_{date}_{ticker}_eq",
                    timestamp=dt.datetime.combine(date, dt.time()),
                    ticker=ticker,
                    action=action,
                    alpha_horizon=AlphaHorizon.SECOND_ORDER,
                    confidence=decision.conviction,
                    rationale=f"[{decision.strategy_name}] {decision.rationale}",
                    status=SignalStatus.PENDING,
                )
                equity_positions.append(BacktestTrade(
                    signal=signal,
                    entry_price=price,
                    entry_date=dt.datetime.combine(date, dt.time()),
                    shares=shares,
                    stop_loss_price=sl_price,
                    take_profit_price=tp_price,
                ))

        # ── Release F&O capital for settled trades ──
        newly_settled = [
            ft for ft in fno_trades
            if ft.exit_date == date and ft.trade_id not in _settled_fno
        ]
        for ft in newly_settled:
            cash += ft.pnl
            fno_capital_at_risk = max(fno_capital_at_risk - ft.entry_cost, 0)
            _settled_fno.add(ft.trade_id)

            idx = ft.underlying
            if ft.pnl < 0:
                _idx_consec_losses[idx] = _idx_consec_losses.get(idx, 0) + 1
                if _idx_consec_losses[idx] >= 3:
                    _idx_cooldown_until[idx] = date + dt.timedelta(days=14)
                elif _idx_consec_losses[idx] >= 2:
                    _idx_cooldown_until[idx] = date + dt.timedelta(days=7)
            else:
                _idx_consec_losses[idx] = 0

        position_value = sum(
            p.shares * date_eq_prices.get(p.signal.ticker, p.entry_price)
            for p in equity_positions
        )
        equity_curve.append(cash + position_value)
        equity_dates.append(date.isoformat())

    # ── Close remaining equity positions ──
    for pos in equity_positions:
        ticker_bars = equity_data.get(pos.signal.ticker, [])
        last_price = ticker_bars[-1].close if ticker_bars else pos.entry_price
        _close_position(pos, last_price, sorted_dates[-1])
        closed_equity.append(pos)
        cash += pos.shares * last_price

    all_closed_equity = closed_equity
    all_fno = [t for t in fno_trades if t.exit_date is not None]
    total_fno_pnl = sum(t.pnl for t in all_fno)
    # equity_curve already includes settled F&O PnL (added to cash on settlement)
    final_equity = equity_curve[-1]

    total_trades = len(all_closed_equity) + len(all_fno)
    fno_winners = len([t for t in all_fno if t.pnl > 0])
    eq_winners = len([t for t in all_closed_equity if t.pnl > 0])
    total_winners = fno_winners + eq_winners

    metrics = _compute_metrics(all_closed_equity, equity_curve, initial_equity, days)
    if total_trades > 0:
        metrics["win_rate"] = total_winners / total_trades * 100

    if initial_equity > 0:
        metrics["total_return_pct"] = (final_equity / initial_equity - 1) * 100

    # Fix: include F&O trades in profit_factor, avg_hold_days, avg_win/loss_pct
    all_pnls: list[float] = [t.pnl for t in all_closed_equity if t.is_closed]
    all_pnls.extend(ft.pnl for ft in all_fno)
    gross_win_all = sum(p for p in all_pnls if p > 0)
    gross_loss_all = abs(sum(p for p in all_pnls if p <= 0))
    metrics["profit_factor"] = gross_win_all / max(gross_loss_all, 1e-10)

    all_hold_days: list[int] = [t.hold_days for t in all_closed_equity if t.is_closed]
    all_hold_days.extend(ft.hold_days for ft in all_fno)
    if all_hold_days:
        metrics["avg_hold_days"] = float(np.mean(all_hold_days))

    win_pcts = [t.pnl_pct for t in all_closed_equity if t.is_closed and t.pnl > 0]
    win_pcts.extend(ft.pnl_pct for ft in all_fno if ft.pnl > 0)
    loss_pcts = [t.pnl_pct for t in all_closed_equity if t.is_closed and t.pnl <= 0]
    loss_pcts.extend(ft.pnl_pct for ft in all_fno if ft.pnl <= 0)
    if win_pcts:
        metrics["avg_win_pct"] = float(np.mean(win_pcts))
    if loss_pcts:
        metrics["avg_loss_pct"] = float(np.mean(loss_pcts))

    # Indices traded
    indices_traded = sorted(set(ft.underlying for ft in all_fno))
    index_country_map = {
        sym: INDEX_FUTURES_SPECS.get(sym, {}).get("country", "?") for sym in indices_traded
    }

    result = BacktestResult(
        start_date=dt.datetime.combine(sorted_dates[0], dt.time()),
        end_date=dt.datetime.combine(sorted_dates[-1], dt.time()),
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return_pct=metrics["total_return_pct"],
        annualized_return_pct=metrics["annualized_return_pct"],
        sharpe_ratio=metrics["sharpe_ratio"],
        max_drawdown_pct=metrics["max_drawdown_pct"],
        total_trades=total_trades,
        winning_trades=total_winners,
        losing_trades=total_trades - total_winners,
        win_rate=metrics["win_rate"],
        avg_win_pct=metrics["avg_win_pct"],
        avg_loss_pct=metrics["avg_loss_pct"],
        avg_hold_days=metrics["avg_hold_days"],
        profit_factor=metrics["profit_factor"],
        trades=all_closed_equity,
        fno_trade_log=all_fno,
        equity_curve=[
            {"date": d, "equity": round(e, 2)}
            for d, e in zip(equity_dates, equity_curve)
        ],
        shocks_analyzed=shocks_analyzed,
        signals_generated=signals_generated,
        signals_executed=total_trades,
        signals_filtered=signals_filtered,
    )

    accepted_r = r_distribution["accepted"]
    realized_r = r_distribution["realized"]
    rejected_r = r_distribution["rejected"]

    r_stats: dict[str, Any] = {
        "trades_accepted": len(accepted_r),
        "trades_rejected_low_r": len(rejected_r),
        "avg_expected_r": round(np.mean(accepted_r), 2) if accepted_r else 0.0,
        "avg_realized_r": round(np.mean(realized_r), 2) if realized_r else 0.0,
        "median_realized_r": round(float(np.median(realized_r)), 2) if realized_r else 0.0,
        "pct_positive_r": round(
            len([r for r in realized_r if r > 0]) / max(len(realized_r), 1) * 100, 1
        ),
        "r_75th": round(float(np.percentile(realized_r, 75)), 2) if realized_r else 0.0,
        "r_25th": round(float(np.percentile(realized_r, 25)), 2) if realized_r else 0.0,
    }
    eq_costs_total = sum(
        equity_costs(t.entry_price, t.exit_price or t.entry_price, t.shares).total
        for t in all_closed_equity
    )
    fno_costs = cost_breakdown["cost_total"]
    result.extra_stats = {
        "fno_trades": len(all_fno),
        "equity_trades": len(all_closed_equity),
        "fno_pnl": round(total_fno_pnl, 2),
        "indices_traded": indices_traded,
        "index_countries": index_country_map,
        "r_distribution": r_stats,
        "transaction_costs": {
            "fno_commission_usd": round(cost_breakdown["commission_total"], 2),
            "fno_slippage_usd": round(cost_breakdown["slippage_total"], 2),
            "fno_costs_usd": round(fno_costs, 2),
            "equity_costs_usd": round(eq_costs_total, 2),
            "total_costs_usd": round(fno_costs + eq_costs_total, 2),
            "cost_drag_pct": round(
                (fno_costs + eq_costs_total) / max(initial_equity, 1) * 100, 3
            ),
            "round_trip_count": cost_breakdown["cost_count"] + len(all_closed_equity),
            "model": "IBKR Tiered (post-cost)",
        },
    }

    log.info(
        f"Backtest complete: {result.total_return_pct:+.2f}% return, "
        f"Sharpe={result.sharpe_ratio:.2f}, MaxDD={result.max_drawdown_pct:.1f}%, "
        f"{result.total_trades} trades ({result.win_rate:.0f}% win rate) "
        f"[{len(all_fno)} index F&O + {len(all_closed_equity)} equity], "
        f"Indices: {', '.join(indices_traded) or 'none'}, "
        f"F&O PnL=${total_fno_pnl:+,.0f}"
    )
    return result


# ── F&O Strategy Helpers ─────────────────────────────────────


def _compute_hurst_fast(closes: np.ndarray, max_lag: int = 20) -> float:
    """Fast Hurst exponent via R/S analysis (inline, no cross-module import)."""
    if len(closes) < max_lag + 2:
        return 0.5
    log_returns = np.diff(np.log(closes))
    lags = range(2, min(max_lag + 1, len(log_returns)))
    rs_list: list[float] = []
    lag_list: list[float] = []
    for lag in lags:
        chunks = [log_returns[i:i + lag] for i in range(0, len(log_returns) - lag + 1, lag)]
        if not chunks:
            continue
        rs_vals: list[float] = []
        for chunk in chunks:
            if len(chunk) < 2:
                continue
            mean = np.mean(chunk)
            deviate = np.cumsum(chunk - mean)
            R = float(np.max(deviate) - np.min(deviate))
            S = float(np.std(chunk, ddof=1))
            if S > 1e-12:
                rs_vals.append(R / S)
        if rs_vals:
            rs_list.append(np.log(np.mean(rs_vals)))
            lag_list.append(np.log(lag))
    if len(rs_list) < 3:
        return 0.5
    coeffs = np.polyfit(lag_list, rs_list, 1)
    return float(np.clip(coeffs[0], 0.0, 1.0))


def _compute_realized_vol(closes: np.ndarray, window: int = 20) -> float:
    if len(closes) < window + 1:
        return 0.0
    log_returns = np.diff(np.log(closes[-window - 1:]))
    return float(np.std(log_returns, ddof=1) * np.sqrt(252))


@dataclass
class FnOTrade:
    """Single index F&O backtest trade (futures, index options, index spreads)."""
    trade_id: str
    strategy: str
    underlying: str  # index symbol: "^GSPC", "^NSEI", etc.
    instrument: str  # "INDEX_FUTURE", "INDEX_CALL", "INDEX_PUT", "INDEX_SPREAD"
    direction: str   # "LONG" or "SHORT"
    entry_date: dt.date
    exit_date: dt.date | None = None
    entry_cost: float = 0.0
    exit_value: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    details: dict = field(default_factory=dict)

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


@dataclass
class FnOBacktestResult:
    strategy: str
    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0
    sharpe: float = 0.0
    trades: list[FnOTrade] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "win_rate": round(self.win_rate, 2),
            "total_pnl": round(self.total_pnl, 2),
            "avg_pnl": round(self.avg_pnl, 2),
            "max_win": round(self.max_win, 2),
            "max_loss": round(self.max_loss, 2),
            "sharpe": round(self.sharpe, 2),
        }


async def run_volatility_arbitrage_backtest(
    tickers: list[str] | None = None,
    days: int = 60,
    iv_rv_threshold: float = 0.08,
    hold_days: int = 5,
) -> FnOBacktestResult:
    """Strategy A — Volatility Arbitrage (Renaissance-style).

    When implied vol significantly deviates from realised vol:
    - IV >> RV → sell straddles (collect overpriced premium)
    - IV << RV → buy straddles (buy underpriced vol)

    Uses Hurst exponent to confirm regime and filter noise.
    """
    from execution_engine.math.options_pricing import straddle_price, black_scholes

    if tickers is None:
        available = sorted(p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0)
        tickers = available[:30] if available else ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG"]

    all_bars: dict[str, list[OHLCVBar]] = {}
    for ticker in tickers:
        bars = _load_local_bars(ticker)
        if bars and len(bars) >= 30:
            all_bars[ticker] = bars

    trades: list[FnOTrade] = []
    all_dates: set[dt.date] = set()
    for bars in all_bars.values():
        for bar in bars:
            all_dates.add(bar.timestamp.date())
    sorted_dates = sorted(all_dates)[-days:]

    for day_idx, date in enumerate(sorted_dates):
        if day_idx >= len(sorted_dates) - hold_days - 1:
            break

        for ticker, bars in all_bars.items():
            closes = np.array([b.close for b in bars if b.timestamp.date() <= date])
            if len(closes) < 30:
                continue

            S = float(closes[-1])
            rv = _compute_realized_vol(closes, 20)
            hurst = _compute_hurst_fast(closes, 15)

            ret_5d = abs(float(closes[-1] / closes[-6] - 1)) if len(closes) >= 6 else 0
            iv_proxy = rv + ret_5d * 2.0 + 0.02
            iv_proxy = max(iv_proxy, 0.10)

            T = hold_days / 252.0
            r = 0.05
            K = S

            diff = iv_proxy - rv

            if abs(diff) < iv_rv_threshold:
                continue

            straddle_info = straddle_price(S, K, T, r, iv_proxy)
            cost = straddle_info["total_cost"]

            exit_idx = day_idx + hold_days
            if exit_idx >= len(sorted_dates):
                continue

            exit_date = sorted_dates[exit_idx]
            exit_closes = np.array([b.close for b in bars if b.timestamp.date() == exit_date])
            if len(exit_closes) == 0:
                continue
            S_exit = float(exit_closes[0])

            call_payoff = max(S_exit - K, 0)
            put_payoff = max(K - S_exit, 0)
            straddle_payoff = call_payoff + put_payoff

            if diff > iv_rv_threshold and hurst < 0.55:
                pnl = (cost - straddle_payoff) * 100
                direction = "SHORT"
                instrument = "STRADDLE"
            elif hurst > 0.55:
                pnl = (straddle_payoff - cost) * 100
                direction = "LONG"
                instrument = "STRADDLE"
            else:
                continue

            trade = FnOTrade(
                trade_id=f"vol_arb_{date}_{ticker}",
                strategy="volatility_arbitrage",
                underlying=ticker,
                instrument=instrument,
                direction=direction,
                entry_date=date,
                exit_date=exit_date,
                entry_cost=cost * 100,
                exit_value=straddle_payoff * 100,
                pnl=pnl,
                pnl_pct=(pnl / max(cost * 100, 0.01)) * 100,
                hold_days=hold_days,
                details={
                    "iv": round(iv_proxy, 4), "rv": round(rv, 4),
                    "iv_rv_diff": round(diff, 4), "hurst": round(hurst, 3),
                    "strike": K, "entry_spot": S, "exit_spot": S_exit,
                },
            )
            trades.append(trade)

    return _build_fno_result("volatility_arbitrage", trades)


async def run_supply_chain_options_backtest(
    tickers: list[str] | None = None,
    days: int = 60,
    shock_threshold: float = 0.05,
    hold_days: int = 7,
) -> FnOBacktestResult:
    """Strategy B — Supply Chain Options Plays.

    When a supply chain shock is detected (large move), buy options on
    the shocked ticker's supply chain neighbours. This is the "entirely
    new strategy" — causal graph intelligence driving options trades.

    In backtest mode, we simulate shocks as days with |return| > threshold,
    and use correlated tickers as supply chain neighbours.
    """
    from execution_engine.math.options_pricing import black_scholes

    if tickers is None:
        available = sorted(p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0)
        tickers = available[:30] if available else ["AAPL", "MSFT", "NVDA"]

    all_bars: dict[str, list[OHLCVBar]] = {}
    for ticker in tickers:
        bars = _load_local_bars(ticker)
        if bars:
            all_bars[ticker] = bars

    # Dynamically build supply chain map from available data correlations
    supply_chain_map = _build_dynamic_supply_chain(all_bars)

    for chain_tickers in supply_chain_map.values():
        for t in chain_tickers:
            if t not in all_bars:
                bars = _load_local_bars(t)
                if bars:
                    all_bars[t] = bars

    trades: list[FnOTrade] = []
    all_dates: set[dt.date] = set()
    for bars in all_bars.values():
        for bar in bars:
            all_dates.add(bar.timestamp.date())
    sorted_dates = sorted(all_dates)[-days:]

    for day_idx, date in enumerate(sorted_dates):
        if day_idx < 1 or day_idx >= len(sorted_dates) - hold_days - 1:
            continue

        for shock_ticker in supply_chain_map:
            if shock_ticker not in all_bars:
                continue

            closes = np.array([b.close for b in all_bars[shock_ticker] if b.timestamp.date() <= date])
            if len(closes) < 5:
                continue

            daily_ret = float(closes[-1] / closes[-2] - 1)
            if abs(daily_ret) < shock_threshold:
                continue

            neighbours = [t for t in supply_chain_map[shock_ticker] if t in all_bars]
            if not neighbours:
                continue

            for neighbour in neighbours:
                n_closes = np.array([b.close for b in all_bars[neighbour] if b.timestamp.date() <= date])
                if len(n_closes) < 10:
                    continue

                S = float(n_closes[-1])
                rv = _compute_realized_vol(n_closes, 15)
                sigma = max(rv, 0.15)
                T = hold_days / 252.0
                r = 0.05

                if daily_ret < -shock_threshold:
                    K = S * 0.97
                    option_type = "PUT"
                    direction = "LONG"
                else:
                    K = S * 1.03
                    option_type = "CALL"
                    direction = "LONG"

                premium = black_scholes(S, K, T, r, sigma, option_type)
                if premium < 0.10:
                    continue

                exit_idx = day_idx + hold_days
                if exit_idx >= len(sorted_dates):
                    continue
                exit_date = sorted_dates[exit_idx]

                n_exit_closes = np.array([b.close for b in all_bars[neighbour] if b.timestamp.date() == exit_date])
                if len(n_exit_closes) == 0:
                    continue

                S_exit = float(n_exit_closes[0])
                if option_type == "CALL":
                    payoff = max(S_exit - K, 0)
                else:
                    payoff = max(K - S_exit, 0)

                pnl = (payoff - premium) * 100

                trade = FnOTrade(
                    trade_id=f"sc_opt_{date}_{neighbour}",
                    strategy="supply_chain_options",
                    underlying=neighbour,
                    instrument=option_type,
                    direction=direction,
                    entry_date=date,
                    exit_date=exit_date,
                    entry_cost=premium * 100,
                    exit_value=payoff * 100,
                    pnl=pnl,
                    pnl_pct=(pnl / max(premium * 100, 0.01)) * 100,
                    hold_days=hold_days,
                    details={
                        "shock_ticker": shock_ticker, "shock_return": round(daily_ret, 4),
                        "strike": K, "sigma": round(sigma, 3),
                        "entry_spot": S, "exit_spot": S_exit,
                    },
                )
                trades.append(trade)

    return _build_fno_result("supply_chain_options", trades)


async def run_iron_condor_backtest(
    tickers: list[str] | None = None,
    days: int = 60,
    wing_width_pct: float = 0.05,
    hold_days: int = 10,
    max_hurst: float = 0.60,
) -> FnOBacktestResult:
    """Strategy C — Iron Condor / Credit Spread Premium Selling.

    On tickers in LOW-TREND regime (Hurst < 0.60), sell iron condors.
    Collect theta decay while the stock stays range-bound.
    """
    from execution_engine.math.options_pricing import iron_condor_price

    if tickers is None:
        available = sorted(p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0)
        tickers = available[:30] if available else ["AAPL", "MSFT", "NVDA"]

    all_bars: dict[str, list[OHLCVBar]] = {}
    for ticker in tickers:
        bars = _load_local_bars(ticker)
        if bars and len(bars) >= 30:
            all_bars[ticker] = bars

    trades: list[FnOTrade] = []
    all_dates: set[dt.date] = set()
    for bars in all_bars.values():
        for bar in bars:
            all_dates.add(bar.timestamp.date())
    sorted_dates = sorted(all_dates)[-days:]

    for day_idx, date in enumerate(sorted_dates):
        if day_idx >= len(sorted_dates) - hold_days - 1:
            break

        for ticker, bars in all_bars.items():
            closes = np.array([b.close for b in bars if b.timestamp.date() <= date])
            if len(closes) < 30:
                continue

            S = float(closes[-1])
            hurst = _compute_hurst_fast(closes, 15)

            if hurst > max_hurst:
                continue

            rsi = _compute_rsi_fast(closes, 14)
            if rsi < 25 or rsi > 75:
                continue

            rv = _compute_realized_vol(closes, 20)
            sigma = max(rv * 1.1, 0.10)
            T = hold_days / 252.0
            r = 0.05

            put_long_K = S * (1 - 2 * wing_width_pct)
            put_short_K = S * (1 - wing_width_pct)
            call_short_K = S * (1 + wing_width_pct)
            call_long_K = S * (1 + 2 * wing_width_pct)

            ic = iron_condor_price(S, T, r, sigma, put_long_K, put_short_K, call_short_K, call_long_K)

            if ic["net_credit"] < 0.05:
                continue
            if ic["risk_reward"] < 0.15:
                continue

            exit_idx = day_idx + hold_days
            if exit_idx >= len(sorted_dates):
                continue
            exit_date = sorted_dates[exit_idx]

            exit_closes = np.array([b.close for b in bars if b.timestamp.date() == exit_date])
            if len(exit_closes) == 0:
                continue
            S_exit = float(exit_closes[0])

            if put_short_K <= S_exit <= call_short_K:
                pnl = ic["net_credit"] * 100
            elif S_exit < put_long_K or S_exit > call_long_K:
                pnl = -ic["max_loss"] * 100
            elif S_exit < put_short_K:
                loss_on_put = put_short_K - max(S_exit, put_long_K)
                pnl = (ic["net_credit"] - loss_on_put) * 100
            else:
                loss_on_call = min(S_exit, call_long_K) - call_short_K
                pnl = (ic["net_credit"] - loss_on_call) * 100

            trade = FnOTrade(
                trade_id=f"ic_{date}_{ticker}",
                strategy="iron_condor",
                underlying=ticker,
                instrument="IRON_CONDOR",
                direction="SHORT",
                entry_date=date,
                exit_date=exit_date,
                entry_cost=ic["max_loss"] * 100,
                exit_value=max(ic["max_loss"] * 100 + pnl, 0),
                pnl=pnl,
                pnl_pct=(pnl / max(ic["max_loss"] * 100, 0.01)) * 100,
                hold_days=hold_days,
                details={
                    "hurst": round(hurst, 3), "rsi": round(rsi, 1),
                    "net_credit": round(ic["net_credit"], 3),
                    "max_loss": round(ic["max_loss"], 3),
                    "put_short_K": round(put_short_K, 2),
                    "call_short_K": round(call_short_K, 2),
                    "entry_spot": S, "exit_spot": S_exit,
                },
            )
            trades.append(trade)

    return _build_fno_result("iron_condor", trades)


async def run_managed_futures_backtest(
    symbols: list[str] | None = None,
    days: int = 60,
    min_hurst: float = 0.55,
    hold_days: int = 10,
    trailing_stop_pct: float = 0.03,
) -> FnOBacktestResult:
    """Strategy D — Managed Futures / CTA.

    Trend-following on futures using Hurst exponent and ADX. When Hurst > 0.55,
    ride momentum with trailing stops. In backtest, we simulate futures using
    equity proxies (multiplier-adjusted PnL).
    """
    if symbols is None:
        available = sorted(p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0)
        symbols = available[:20] if available else ["AAPL", "MSFT"]

    all_bars: dict[str, list[OHLCVBar]] = {}
    for sym in symbols:
        bars = _load_local_bars(sym)
        if bars and len(bars) >= 30:
            all_bars[sym] = bars

    trades: list[FnOTrade] = []
    all_dates: set[dt.date] = set()
    for bars in all_bars.values():
        for bar in bars:
            all_dates.add(bar.timestamp.date())
    sorted_dates = sorted(all_dates)[-days:]

    open_positions: dict[str, dict] = {}

    for day_idx, date in enumerate(sorted_dates):
        for sym, bars in all_bars.items():
            closes = np.array([b.close for b in bars if b.timestamp.date() <= date])
            if len(closes) < 25:
                continue
            current_price = float(closes[-1])

            if sym in open_positions:
                pos = open_positions[sym]
                if pos["direction"] == "LONG":
                    new_trail = current_price * (1 - trailing_stop_pct)
                    pos["trailing_stop"] = max(pos["trailing_stop"], new_trail)
                    if current_price <= pos["trailing_stop"] or (date - pos["entry_date"]).days >= hold_days:
                        pnl = (current_price - pos["entry_price"]) * pos["multiplier"]
                        trades.append(FnOTrade(
                            trade_id=f"cta_{pos['entry_date']}_{sym}",
                            strategy="managed_futures",
                            underlying=sym,
                            instrument="FUTURE",
                            direction="LONG",
                            entry_date=pos["entry_date"],
                            exit_date=date,
                            entry_cost=pos["entry_price"] * pos["multiplier"],
                            exit_value=current_price * pos["multiplier"],
                            pnl=pnl,
                            pnl_pct=(current_price / pos["entry_price"] - 1) * 100,
                            hold_days=(date - pos["entry_date"]).days,
                            details={"hurst": pos["hurst"], "multiplier": pos["multiplier"]},
                        ))
                        del open_positions[sym]
                else:
                    new_trail = current_price * (1 + trailing_stop_pct)
                    pos["trailing_stop"] = min(pos["trailing_stop"], new_trail)
                    if current_price >= pos["trailing_stop"] or (date - pos["entry_date"]).days >= hold_days:
                        pnl = (pos["entry_price"] - current_price) * pos["multiplier"]
                        trades.append(FnOTrade(
                            trade_id=f"cta_{pos['entry_date']}_{sym}",
                            strategy="managed_futures",
                            underlying=sym,
                            instrument="FUTURE",
                            direction="SHORT",
                            entry_date=pos["entry_date"],
                            exit_date=date,
                            entry_cost=pos["entry_price"] * pos["multiplier"],
                            exit_value=current_price * pos["multiplier"],
                            pnl=pnl,
                            pnl_pct=(pos["entry_price"] / current_price - 1) * 100,
                            hold_days=(date - pos["entry_date"]).days,
                            details={"hurst": pos["hurst"], "multiplier": pos["multiplier"]},
                        ))
                        del open_positions[sym]
                continue

            if sym in open_positions:
                continue
            if day_idx >= len(sorted_dates) - hold_days - 1:
                continue

            hurst = _compute_hurst_fast(closes, 15)
            if hurst < min_hurst:
                continue

            sma_20 = float(np.mean(closes[-20:]))
            sma_5 = float(np.mean(closes[-5:]))

            multiplier = 50.0

            if sma_5 > sma_20 and current_price > sma_5:
                open_positions[sym] = {
                    "entry_price": current_price,
                    "entry_date": date,
                    "direction": "LONG",
                    "trailing_stop": current_price * (1 - trailing_stop_pct),
                    "hurst": round(hurst, 3),
                    "multiplier": multiplier,
                }
            elif sma_5 < sma_20 and current_price < sma_5:
                open_positions[sym] = {
                    "entry_price": current_price,
                    "entry_date": date,
                    "direction": "SHORT",
                    "trailing_stop": current_price * (1 + trailing_stop_pct),
                    "hurst": round(hurst, 3),
                    "multiplier": multiplier,
                }

    return _build_fno_result("managed_futures", trades)


def _build_fno_result(strategy: str, trades: list[FnOTrade]) -> FnOBacktestResult:
    winners = [t for t in trades if t.pnl > 0]
    pnls = [t.pnl for t in trades]
    total_pnl = sum(pnls) if pnls else 0
    avg_pnl = float(np.mean(pnls)) if pnls else 0
    sharpe = float(np.mean(pnls) / max(np.std(pnls), 1e-10)) if len(pnls) > 1 else 0

    return FnOBacktestResult(
        strategy=strategy,
        total_trades=len(trades),
        winning_trades=len(winners),
        win_rate=len(winners) / max(len(trades), 1) * 100,
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        max_win=max(pnls) if pnls else 0,
        max_loss=min(pnls) if pnls else 0,
        sharpe=sharpe,
        trades=trades,
    )


async def run_full_fno_backtest(
    tickers: list[str] | None = None,
    days: int = 60,
) -> dict[str, Any]:
    """Run all F&O strategies and return combined results."""
    vol_arb = await run_volatility_arbitrage_backtest(tickers, days)
    sc_opts = await run_supply_chain_options_backtest(tickers, days)
    condors = await run_iron_condor_backtest(tickers, days)
    cta = await run_managed_futures_backtest(tickers, days)

    strategies = [vol_arb, sc_opts, condors, cta]
    total_trades = sum(s.total_trades for s in strategies)
    total_winners = sum(s.winning_trades for s in strategies)
    total_pnl = sum(s.total_pnl for s in strategies)

    return {
        "summary": {
            "total_strategies": 4,
            "total_trades": total_trades,
            "total_winners": total_winners,
            "combined_win_rate": round(total_winners / max(total_trades, 1) * 100, 2),
            "combined_pnl": round(total_pnl, 2),
        },
        "strategies": {s.strategy: s.to_dict() for s in strategies},
    }
