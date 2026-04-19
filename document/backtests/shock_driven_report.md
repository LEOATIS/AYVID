# AYVID Shock-Driven Walk-Forward Report

*Generated: 2026-04-19 15:08 UTC*
*Min conviction filter: 0.55*

**This is AYVID's actual operating mode.** Rather than blindly trading every ticker every day on technicals (the naive walk-forward baseline), this report restricts the system to named historical shocks where the graph layer identified a real causal event, and only trades the tickers the graph surfaced as causally linked. PnL is post-cost using the IBKR Tiered model.

## Setup

| Parameter | Value |
|-----------|-------|
| Span | 2018-01-01 → 2025-12-31 |
| Catalog size | 20 named shocks |
| Scenarios run | 20 |
| Valid (non-empty) | 20 |
| Initial equity per scenario | $1,000,000 |
| Avg trades / scenario | 67.2 |
| Pooled trades total | 1,343 |

## Distribution of Outcomes

| Metric | Value |
|--------|-------|
| Avg return per scenario | +5.33% |
| Median return per scenario | -4.97% |
| % positive scenarios | 35.0% |
| Avg Sharpe | -1.13 |
| Median Sharpe | -0.63 |
| % Sharpe > 1.0 | 30.0% |
| Worst single-scenario DD | 43.80% |
| Avg drawdown | 11.82% |
| Pooled win rate | 34.0% |
| Avg cost drag / scenario | 1.934% |

## Compounded Aggregate (chained scenarios)

| Metric | Value |
|--------|-------|
| Total compounded return | +43.65% |
| CAGR | +4.63% |

## Per-Scenario Results

| Scenario | Window | Return | Sharpe | Max DD | Trades | Tickers | Notes |
|----------|--------|--------|--------|--------|--------|---------|-------|
| US-China Trade War Escalation | 2018-06-01 → 2018-12-31 | +5.63% | 0.60 | 6.57% | 57 | 7eq+2idx | trade_war |
| Q4 2018 Powell Tightening Selloff | 2018-10-01 → 2018-12-26 | +70.68% | 2.25 | 12.69% | 65 | 9eq+3idx | monetary_tightening |
| Boeing 737 MAX Grounding | 2019-03-10 → 2019-06-28 | -4.03% | -2.90 | 4.82% | 20 | 4eq+2idx | regulatory_grounding |
| COVID-19 Market Crash | 2020-02-15 → 2020-04-30 | +98.86% | 3.09 | 14.68% | 106 | 10eq+4idx | pandemic |
| Post-COVID Tech Megacap Rally | 2020-04-15 → 2020-12-31 | -2.38% | -0.06 | 14.40% | 124 | 11eq+2idx | monetary_easing |
| ARK / Hyper-Growth Selloff | 2021-02-15 → 2021-08-31 | -11.26% | -3.11 | 14.08% | 40 | 7eq+2idx | rotation |
| Suez Canal Blockage | 2021-03-20 → 2021-05-15 | +2.21% | 1.05 | 3.38% | 9 | 5eq+2idx | supply_disruption |
| China Big-Tech Regulatory Crackdown | 2021-07-01 → 2021-12-31 | -6.63% | -1.53 | 7.77% | 48 | 9eq+2idx | regulatory |
| 2022 Fed Hike Cycle / Tech Bear | 2022-01-03 → 2022-10-14 | -36.22% | -2.77 | 43.80% | 227 | 11eq+3idx | monetary_tightening |
| Russia-Ukraine Invasion | 2022-02-20 → 2022-05-31 | +10.30% | 1.69 | 4.06% | 23 | 4eq+3idx | geopolitical |
| Semiconductor Inventory Correction | 2022-06-01 → 2022-12-31 | -27.19% | -3.85 | 31.90% | 145 | 14eq+3idx | demand_reset |
| UK Mini-Budget / Gilt Crisis | 2022-09-15 → 2022-10-31 | -6.44% | -8.37 | 6.58% | 12 | 6eq+1idx | fiscal_shock |
| ChatGPT / Generative-AI Rally | 2023-01-15 → 2023-07-31 | -7.68% | -0.59 | 11.61% | 163 | 15eq+4idx | ai_capex |
| SVB / Regional Bank Crisis | 2023-03-01 → 2023-04-30 | +29.11% | 2.19 | 8.39% | 23 | 5eq+3idx | banking_crisis |
| Hamas-Israel War | 2023-10-07 → 2023-12-31 | -5.91% | -3.40 | 8.17% | 14 | 3eq+2idx | geopolitical |
| Red Sea / Houthi Shipping Attacks | 2023-11-15 → 2024-03-31 | -9.99% | -4.94 | 10.44% | 26 | 6eq+2idx | supply_disruption |
| NVIDIA Q4 Blowout / AI Capex Phase 2 | 2024-02-01 → 2024-06-28 | -5.91% | -0.67 | 12.46% | 120 | 15eq+4idx | ai_capex |
| Yen Carry Trade Unwind | 2024-07-15 → 2024-09-15 | -0.85% | -0.18 | 7.07% | 34 | 7eq+3idx | fx_unwind |
| Trump 2.0 Election Rally | 2024-11-01 → 2024-12-31 | -9.69% | -4.14 | 11.08% | 35 | 7eq+3idx | political |
| DeepSeek R1 / China AI Shock | 2025-01-20 → 2025-03-31 | +23.92% | 3.09 | 2.49% | 52 | 10eq+3idx | ai_capex_doubt |

---
*AYVID Shock-Driven Walk-Forward — selective, event-driven, post-cost*
