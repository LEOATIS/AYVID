# AYVID Shock-Driven Walk-Forward Report

*Generated: 2026-04-19 15:09 UTC*
*Min conviction filter: 0.70*

**This is AYVID's actual operating mode.** Rather than blindly trading every ticker every day on technicals (the naive walk-forward baseline), this report restricts the system to named historical shocks where the graph layer identified a real causal event, and only trades the tickers the graph surfaced as causally linked. PnL is post-cost using the IBKR Tiered model.

## Setup

| Parameter | Value |
|-----------|-------|
| Span | 2018-01-01 → 2025-12-31 |
| Catalog size | 20 named shocks |
| Scenarios run | 20 |
| Valid (non-empty) | 20 |
| Initial equity per scenario | $1,000,000 |
| Avg trades / scenario | 64.2 |
| Pooled trades total | 1,285 |

## Distribution of Outcomes

| Metric | Value |
|--------|-------|
| Avg return per scenario | +5.49% |
| Median return per scenario | -4.84% |
| % positive scenarios | 35.0% |
| Avg Sharpe | -1.14 |
| Median Sharpe | -0.66 |
| % Sharpe > 1.0 | 30.0% |
| Worst single-scenario DD | 44.22% |
| Avg drawdown | 11.94% |
| Pooled win rate | 32.8% |
| Avg cost drag / scenario | 1.920% |

## Compounded Aggregate (chained scenarios)

| Metric | Value |
|--------|-------|
| Total compounded return | +45.03% |
| CAGR | +4.76% |

## Per-Scenario Results

| Scenario | Window | Return | Sharpe | Max DD | Trades | Tickers | Notes |
|----------|--------|--------|--------|--------|--------|---------|-------|
| US-China Trade War Escalation | 2018-06-01 → 2018-12-31 | +6.33% | 0.66 | 6.12% | 56 | 7eq+2idx | trade_war |
| Q4 2018 Powell Tightening Selloff | 2018-10-01 → 2018-12-26 | +74.91% | 2.24 | 13.03% | 59 | 9eq+3idx | monetary_tightening |
| Boeing 737 MAX Grounding | 2019-03-10 → 2019-06-28 | -3.76% | -2.54 | 4.80% | 20 | 4eq+2idx | regulatory_grounding |
| COVID-19 Market Crash | 2020-02-15 → 2020-04-30 | +98.85% | 3.09 | 14.68% | 106 | 10eq+4idx | pandemic |
| Post-COVID Tech Megacap Rally | 2020-04-15 → 2020-12-31 | -3.77% | -0.16 | 14.64% | 118 | 11eq+2idx | monetary_easing |
| ARK / Hyper-Growth Selloff | 2021-02-15 → 2021-08-31 | -11.15% | -3.10 | 14.05% | 39 | 7eq+2idx | rotation |
| Suez Canal Blockage | 2021-03-20 → 2021-05-15 | +2.13% | 1.02 | 3.35% | 8 | 5eq+2idx | supply_disruption |
| China Big-Tech Regulatory Crackdown | 2021-07-01 → 2021-12-31 | -7.40% | -1.87 | 8.60% | 44 | 9eq+2idx | regulatory |
| 2022 Fed Hike Cycle / Tech Bear | 2022-01-03 → 2022-10-14 | -36.28% | -2.73 | 44.22% | 213 | 11eq+3idx | monetary_tightening |
| Russia-Ukraine Invasion | 2022-02-20 → 2022-05-31 | +9.98% | 1.65 | 4.08% | 21 | 4eq+3idx | geopolitical |
| Semiconductor Inventory Correction | 2022-06-01 → 2022-12-31 | -27.33% | -3.82 | 32.15% | 132 | 14eq+3idx | demand_reset |
| UK Mini-Budget / Gilt Crisis | 2022-09-15 → 2022-10-31 | -6.44% | -8.37 | 6.58% | 12 | 6eq+1idx | fiscal_shock |
| ChatGPT / Generative-AI Rally | 2023-01-15 → 2023-07-31 | -7.01% | -0.51 | 11.36% | 160 | 15eq+4idx | ai_capex |
| SVB / Regional Bank Crisis | 2023-03-01 → 2023-04-30 | +30.68% | 2.20 | 8.52% | 22 | 5eq+3idx | banking_crisis |
| Hamas-Israel War | 2023-10-07 → 2023-12-31 | -5.91% | -3.40 | 8.17% | 14 | 3eq+2idx | geopolitical |
| Red Sea / Houthi Shipping Attacks | 2023-11-15 → 2024-03-31 | -9.77% | -5.08 | 10.22% | 24 | 6eq+2idx | supply_disruption |
| NVIDIA Q4 Blowout / AI Capex Phase 2 | 2024-02-01 → 2024-06-28 | -6.86% | -0.80 | 13.33% | 118 | 15eq+4idx | ai_capex |
| Yen Carry Trade Unwind | 2024-07-15 → 2024-09-15 | -0.85% | -0.19 | 7.07% | 34 | 7eq+3idx | fx_unwind |
| Trump 2.0 Election Rally | 2024-11-01 → 2024-12-31 | -10.09% | -4.22 | 11.25% | 34 | 7eq+3idx | political |
| DeepSeek R1 / China AI Shock | 2025-01-20 → 2025-03-31 | +23.48% | 3.05 | 2.50% | 51 | 10eq+3idx | ai_capex_doubt |

---
*AYVID Shock-Driven Walk-Forward — selective, event-driven, post-cost*
