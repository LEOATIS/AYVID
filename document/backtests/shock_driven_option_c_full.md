# AYVID Shock-Driven Walk-Forward Report

*Generated: 2026-04-29 19:01 UTC*
*Min conviction filter: 0.55*

**This is AYVID's actual operating mode.** Rather than blindly trading every ticker every day on technicals (the naive walk-forward baseline), this report restricts the system to named historical shocks where the graph layer identified a real causal event, and only trades the tickers the graph surfaced as causally linked. PnL is post-cost using the IBKR Tiered model.

## Setup

| Parameter | Value |
|-----------|-------|
| Span | 2018-01-01 → 2026-04-30 |
| Catalog size | 20 named shocks |
| Scenarios run | 20 |
| Valid (non-empty) | 20 |
| Initial equity per scenario | $1,000,000 |
| Avg trades / scenario | 33.6 |
| Pooled trades total | 673 |

## Distribution of Outcomes

| Metric | Value |
|--------|-------|
| Avg return per scenario | +1.34% |
| Median return per scenario | -0.07% |
| % positive scenarios | 40.0% |
| Avg Sharpe | -0.09 |
| Median Sharpe | -0.18 |
| % Sharpe > 1.0 | 25.0% |
| Worst single-scenario DD | 11.91% |
| Avg drawdown | 3.31% |
| Pooled win rate | 33.4% |
| Avg cost drag / scenario | 0.504% |

## Compounded Aggregate (chained scenarios)

| Metric | Value |
|--------|-------|
| Total compounded return | +24.13% |
| CAGR | +2.63% |

## Per-Scenario Results

| Scenario | Window | Return | Sharpe | Max DD | Trades | Tickers | Notes |
|----------|--------|--------|--------|--------|--------|---------|-------|
| US-China Trade War Escalation | 2018-06-01 → 2018-12-31 | +1.29% | 0.29 | 3.41% | 38 | 7eq+2idx | trade_war |
| Q4 2018 Powell Tightening Selloff | 2018-10-01 → 2018-12-26 | +0.14% | 0.11 | 3.03% | 19 | 9eq+3idx | monetary_tightening |
| Boeing 737 MAX Grounding | 2019-03-10 → 2019-06-28 | -0.06% | -0.37 | 0.31% | 1 | 4eq+2idx | regulatory_grounding |
| COVID-19 Market Crash | 2020-02-15 → 2020-04-30 | -4.04% | -1.82 | 7.16% | 47 | 10eq+4idx | pandemic |
| Post-COVID Tech Megacap Rally | 2020-04-15 → 2020-12-31 | -4.86% | -0.83 | 6.65% | 113 | 11eq+2idx | monetary_easing |
| ARK / Hyper-Growth Selloff | 2021-02-15 → 2021-08-31 | -1.87% | -0.88 | 4.46% | 20 | 7eq+2idx | rotation |
| Suez Canal Blockage | 2021-03-20 → 2021-05-15 | +0.49% | 4.54 | 0.06% | 8 | 5eq+2idx | supply_disruption |
| China Big-Tech Regulatory Crackdown | 2021-07-01 → 2021-12-31 | -4.21% | -2.32 | 4.29% | 18 | 9eq+2idx | regulatory |
| 2022 Fed Hike Cycle / Tech Bear | 2022-01-03 → 2022-10-14 | -0.30% | 0.01 | 11.91% | 71 | 11eq+3idx | monetary_tightening |
| Russia-Ukraine Invasion | 2022-02-20 → 2022-05-31 | -0.08% | -0.53 | 0.24% | 6 | 4eq+3idx | geopolitical |
| Semiconductor Inventory Correction | 2022-06-01 → 2022-12-31 | -3.00% | -1.82 | 3.08% | 19 | 14eq+3idx | demand_reset |
| UK Mini-Budget / Gilt Crisis | 2022-09-15 → 2022-10-31 | -2.01% | 0.00 | 0.00% | 5 | 6eq+1idx | fiscal_shock |
| ChatGPT / Generative-AI Rally | 2023-01-15 → 2023-07-31 | +4.24% | 0.71 | 5.11% | 143 | 15eq+4idx | ai_capex |
| SVB / Regional Bank Crisis | 2023-03-01 → 2023-04-30 | +23.83% | 2.69 | 2.17% | 12 | 5eq+3idx | banking_crisis |
| Hamas-Israel War | 2023-10-07 → 2023-12-31 | -0.02% | -0.65 | 0.06% | 2 | 3eq+2idx | geopolitical |
| Red Sea / Houthi Shipping Attacks | 2023-11-15 → 2024-03-31 | +0.78% | 1.07 | 0.66% | 16 | 6eq+2idx | supply_disruption |
| NVIDIA Q4 Blowout / AI Capex Phase 2 | 2024-02-01 → 2024-06-28 | -7.06% | -4.81 | 7.46% | 62 | 15eq+4idx | ai_capex |
| Yen Carry Trade Unwind | 2024-07-15 → 2024-09-15 | +4.01% | 2.04 | 1.36% | 24 | 7eq+3idx | fx_unwind |
| Trump 2.0 Election Rally | 2024-11-01 → 2024-12-31 | -1.32% | -2.34 | 2.10% | 10 | 7eq+3idx | political |
| DeepSeek R1 / China AI Shock | 2025-01-20 → 2025-03-31 | +20.88% | 3.13 | 2.61% | 39 | 10eq+3idx | ai_capex_doubt |

---
*AYVID Shock-Driven Walk-Forward — selective, event-driven, post-cost*
